from sqlalchemy import create_engine, text


def get_engine():
    return create_engine("sqlite://")


class Repo:
    def __init__(self, url):
        self.engine = create_engine(url)

    def count(self):
        return self.engine.execute("select count(*) from t")  # BREAK

    def safe_count(self):
        with self.engine.connect() as conn:
            return conn.execute(text("select count(*) from t"))


engine = get_engine()
rows = engine.execute("select 1")  # BREAK
other = get_engine().execute("select 2")  # BREAK
