"""Durable billing journal. Memory restore never rewinds potential API spending."""
from dataclasses import asdict, dataclass, replace
from contextlib import closing
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import uuid

from .models import taipei_now


class AIError(RuntimeError):
    pass


class QuietBudgetExhausted(AIError):
    pass


@dataclass(frozen=True)
class AIConfig:
    model: str = 'gpt-4.1-mini'
    input_usd_per_million: float = 0.40
    output_usd_per_million: float = 1.60
    twd_per_usd: float = 33.0
    monthly_budget_twd: float = 500.0
    max_output_tokens: int = 2000
    minimum_interval_seconds: int = 120
    transmission_approved: bool = False
    key_revision: str = ''
    quiet_interval_minutes: int = 6
    quiet_daily_budget_twd: float = 10.0

    def validate(self):
        # Other model families have different image accounting / output controls.
        if self.model not in {'gpt-4.1-mini', 'gpt-4.1', 'gpt-4o-mini'}:
            raise AIError('這個最小版本支援 gpt-4.1-mini、gpt-4.1、gpt-4o-mini；請核對所選模型價格。')
        for value in (self.input_usd_per_million, self.output_usd_per_million, self.twd_per_usd, self.monthly_budget_twd, self.quiet_daily_budget_twd):
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise AIError('價格、匯率與月預算必須為大於零的有效數字。')
        if not 256 <= self.max_output_tokens <= 2000 or not 60 <= self.minimum_interval_seconds <= 3600:
            raise AIError('輸出上限或 AI 最短間隔不正確。')
        if type(self.quiet_interval_minutes) is not int or not 3 <= self.quiet_interval_minutes <= 30:
            raise AIError('安靜聊天冷卻需為 3–30 分鐘。')

    @property
    def fingerprint(self):
        value = asdict(self)
        # Budget and interval edits do not invalidate a completed model cost test.
        for key in ('monthly_budget_twd', 'minimum_interval_seconds', 'transmission_approved', 'quiet_interval_minutes', 'quiet_daily_budget_twd'):
            value.pop(key)
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def cost_units(config, input_tokens, output_tokens):
    """Integer millionths of NT$, rounded UP. Cached input billed conservatively."""
    return int(((Decimal(str(config.input_usd_per_million)) * input_tokens +
                 Decimal(str(config.output_usd_per_million)) * output_tokens) *
                Decimal(str(config.twd_per_usd))).to_integral_value(rounding=ROUND_CEILING))


class UsageLedger:
    def __init__(self, path):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS requests(
          id TEXT PRIMARY KEY,created_at TEXT NOT NULL,kind TEXT NOT NULL,
          model TEXT NOT NULL,prices TEXT NOT NULL,status TEXT NOT NULL,
          reserved INTEGER NOT NULL,charged INTEGER NOT NULL,
          input_tokens INTEGER,output_tokens INTEGER,provider_id TEXT);
        CREATE TABLE IF NOT EXISTS tests(fingerprint TEXT NOT NULL,kind TEXT NOT NULL,
          request_id TEXT NOT NULL,PRIMARY KEY(fingerprint,kind));
        ''')
        self.db.execute("PRAGMA application_id=1330397489")
        # On a crash, an in-flight request may already have been billed.
        with self.db:
            self.db.execute("UPDATE requests SET status='interrupted' WHERE status='reserved'")

    def config(self):
        row = self.db.execute("SELECT value FROM config WHERE key='ai'").fetchone()
        value = AIConfig(**json.loads(row[0])) if row else AIConfig()
        # User requested a shared 2000-token cap; upgrade older stored 2500+ caps.
        if value.max_output_tokens > 2000:
            value = replace(value, max_output_tokens=2000)
            self.save_config(value)
        value.validate()
        return value

    def save_config(self, value):
        value.validate()
        with self.db:
            self.db.execute("INSERT INTO config VALUES('ai',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(asdict(value)),))

    def totals(self):
        now = taipei_now().isoformat()
        result = {}
        for period, prefix in [('day', now[:10]), ('month', now[:7])]:
            row = self.db.execute('SELECT COUNT(*),COALESCE(SUM(charged),0),COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),COALESCE(SUM(CASE WHEN status!=\'settled\' THEN charged ELSE 0 END),0) FROM requests WHERE created_at LIKE ?', (prefix + '%',)).fetchone()
            result[period] = {'requests': row[0], 'twd': row[1] / 1_000_000, 'input_tokens': row[2], 'output_tokens': row[3], 'uncertain_twd': row[4] / 1_000_000}
        return result

    def reserve(self, config, kind, input_upper_bound):
        config.validate()
        units = cost_units(config, input_upper_bound, config.max_output_tokens)
        request_id = uuid.uuid4().hex
        now = taipei_now().isoformat()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            total = self.db.execute('SELECT COALESCE(SUM(charged),0) FROM requests WHERE created_at LIKE ?', (now[:7] + '%',)).fetchone()[0]
            if total + units > int(Decimal(str(config.monthly_budget_twd)) * 1_000_000):
                raise AIError('月預算不足以預留這次請求；已停止付費呼叫。可調整預算或等下個月。')
            if kind in {'quiet_chat', 'quiet_test'}:
                daily = self.db.execute("SELECT COALESCE(SUM(charged),0) FROM requests WHERE created_at LIKE ? AND kind IN ('quiet_chat','quiet_test')", (now[:10] + '%',)).fetchone()[0]
                if daily + units > int(Decimal(str(config.quiet_daily_budget_twd)) * 1_000_000):
                    raise QuietBudgetExhausted('今日安靜聊天額度不足以預留下一次；明日再試，文字／語音回覆仍可使用。')
            self.db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                            (request_id, now, kind, config.model, json.dumps(asdict(config)), 'reserved', units, units, None, None, None,))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return request_id, units / 1_000_000

    def quiet_today(self):
        return self.db.execute("SELECT COALESCE(SUM(charged),0) FROM requests WHERE created_at LIKE ? AND kind IN ('quiet_chat','quiet_test')", (taipei_now().isoformat()[:10] + '%',)).fetchone()[0] / 1_000_000

    def settle(self, request_id, usage, provider_id):
        if not isinstance(usage, dict):
            raise AIError('API 沒有回報可結算用量；保留預留費用。')
        for name in ('prompt_tokens', 'completion_tokens'):
            if type(usage.get(name)) is not int or usage[name] < 0:
                raise AIError('API 用量不完整；保留預留費用。')
        row = self.db.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
        config = AIConfig(**json.loads(row['prices']))
        units = cost_units(config, usage['prompt_tokens'], usage['completion_tokens'])
        with self.db:
            self.db.execute("UPDATE requests SET status='settled',charged=?,input_tokens=?,output_tokens=?,provider_id=? WHERE id=?", (units, usage['prompt_tokens'], usage['completion_tokens'], provider_id, request_id))
        if units > row['reserved']:
            raise AIError('供應商用量超過預留估算；費用已記錄，AI 已停止。請核對模型與价格。')
        return units / 1_000_000

    def abandon(self, request_id):
        with self.db:
            self.db.execute("UPDATE requests SET status='uncertain' WHERE id=? AND status='reserved'", (request_id,))

    def mark_test(self, config, kind, request_id):
        row = self.db.execute("SELECT status FROM requests WHERE id=?", (request_id,)).fetchone()
        if kind not in {'screen_test', 'reply_test'} or not row or row[0] != 'settled':
            raise AIError('只有回報實際用量且成功的單次測試才能通過。')
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO tests VALUES(?,?,?)', (config.fingerprint, kind, request_id))

    def tests_passed(self, config):
        return {r[0] for r in self.db.execute('SELECT kind FROM tests WHERE fingerprint=?', (config.fingerprint,))} == {'screen_test', 'reply_test'}

    def backup(self, destination):
        destination = Path(destination).resolve()
        if destination.exists():
            raise AIError("備份檔已存在，請選擇新檔名。")
        destination.touch(exist_ok=False)
        try:
            with closing(sqlite3.connect(destination)) as output:
                self.db.backup(output)
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    def merge_backup(self, source):
        """Restore missing spending, never replace a journal with an older snapshot."""
        source = Path(source).resolve()
        if not source.is_file() or source == self.path.resolve():
            raise AIError("請選擇另一份有效的 API 用量備份。")
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as incoming:
            incoming.row_factory = sqlite3.Row
            if incoming.execute("PRAGMA application_id").fetchone()[0] != 1330397489 or incoming.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise AIError("不是完整的 API 用量備份，不會替換目前紀錄。")
            rows = [dict(row) for row in incoming.execute("SELECT * FROM requests")]
            config_row = incoming.execute("SELECT value FROM config WHERE key='ai'").fetchone()
            restored_config = AIConfig(**json.loads(config_row[0])) if config_row else None
            if restored_config:
                restored_config.validate()
        for row in rows:
            cfg = AIConfig(**json.loads(row['prices']))
            cfg.validate()
            stamp = datetime.fromisoformat(row['created_at'])
            if stamp.utcoffset() is None or row['model'] != cfg.model or row['status'] not in {'reserved', 'interrupted', 'uncertain', 'settled'}:
                raise AIError("用量備份格式不正確。")
            if any(type(row[key]) is not int or row[key] < 0 for key in ('charged', 'reserved')):
                raise AIError("用量備份金額不正確。")
            if row['status'] == 'settled' and any(type(row[key]) is not int or row[key] < 0 for key in ('input_tokens', 'output_tokens')):
                raise AIError("用量備份 token 不正確。")
        changed = 0
        with self.db:
            for row in rows:
                existing = self.db.execute("SELECT * FROM requests WHERE id=?", (row['id'],)).fetchone()
                if existing:
                    if any(existing[k] != row[k] for k in ('created_at', 'kind', 'model', 'prices')):
                        raise AIError("用量備份有衝突，已取消合併並保留原資料。")
                    # A settled actual estimate can replace an old uncertainty;
                    # two settled records retain the larger estimate.
                    if row['status'] != 'settled' or (existing['status'] == 'settled' and existing['charged'] >= row['charged']):
                        continue
                    self.db.execute("UPDATE requests SET status=?,charged=?,input_tokens=?,output_tokens=?,provider_id=? WHERE id=?", (row['status'], row['charged'], row['input_tokens'], row['output_tokens'], row['provider_id'], row['id']))
                else:
                    if row['status'] == 'reserved':
                        row['status'] = 'interrupted'
                    keys = ['id','created_at','kind','model','prices','status','reserved','charged','input_tokens','output_tokens','provider_id']
                    self.db.execute("INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?)", tuple(row[k] for k in keys))
                changed += 1
            if restored_config and not self.db.execute("SELECT 1 FROM config WHERE key='ai'").fetchone():
                self.db.execute("INSERT INTO config VALUES('ai',?)", (json.dumps(asdict(restored_config)),))
        return changed

    def close(self):
        self.db.close()
