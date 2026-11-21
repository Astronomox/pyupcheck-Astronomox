from sqlalchemy import create_engine, inspect

engine = create_engine("sqlite://")
names = engine.table_names()  # BREAK
ok = inspect(engine).get_table_names()
