from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

_engine = None
_factory = None


def get_session_factory() -> sessionmaker:
    global _engine, _factory
    if _factory is None:
        st = get_settings()
        connect_args = {"prepare_threshold": None} if st.db_disable_prepared_statements else {}
        _engine = create_engine(st.database_url, pool_pre_ping=True, pool_size=st.db_pool_size,
                                max_overflow=st.db_max_overflow, connect_args=connect_args)
        _factory = sessionmaker(_engine, expire_on_commit=False)
    return _factory


@contextmanager
def session_scope():
    s = get_session_factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
