"""The pinned Windows wheel is webrtcvad-wheels, importing as webrtcvad."""
from PyInstaller.utils.hooks import copy_metadata

datas = copy_metadata('webrtcvad-wheels')
hiddenimports = ['_webrtcvad']
