"""Regional response v2: a separate, source-grounded coordination prototype."""

__all__ = ['RegionalResponseEnv']


def __getattr__(name):
    if name == 'RegionalResponseEnv':
        from .environment import RegionalResponseEnv
        return RegionalResponseEnv
    raise AttributeError(name)
