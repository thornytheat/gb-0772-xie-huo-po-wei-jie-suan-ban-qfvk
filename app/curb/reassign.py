from app.curb.rebooking import *  # noqa: F401,F403
from app.curb import rebooking as _core


def __getattr__(name):
    return _core.__getattr__(name)
