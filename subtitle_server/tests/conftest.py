import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET", "unit-test-secret-unit-test-secret")
os.environ.setdefault("DATA_DIR", os.environ.get("TEMP", "/tmp"))
