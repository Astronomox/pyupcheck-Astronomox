from sqlalchemy import create_engine, text

engine = create_engine("sqlite://")
rows = engine.execute("select 1")  # BREAK
with engine.connect() as conn:
    conn.execute(text("select 1"))
