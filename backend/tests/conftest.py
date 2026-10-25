import os
os.environ["DATABASE_URL"] = "sqlite:///./test_hcm.db"
os.environ["JWT_SECRET"] = "test"
if os.path.exists("test_hcm.db"): os.remove("test_hcm.db")

import pytest
@pytest.fixture(autouse=True)
def fresh_db():
    from db import Base, engine
    import models  # noqa
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    yield
