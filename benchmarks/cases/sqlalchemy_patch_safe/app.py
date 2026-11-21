from sqlalchemy import create_engine, select, text

engine = create_engine("sqlite://")
with engine.connect() as conn:
    conn.execute(text("select 1"))
