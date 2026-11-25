import subprocess
import sys
import textwrap


def test_database_models_survive_module_reimports(tmp_path):
    code = textwrap.dedent("""
        import importlib
        import sys
        import warnings
        from datetime import UTC, datetime

        from sqlalchemy.exc import SAWarning
        from sqlmodel import Session, select

        import sprout.db as original

        warnings.simplefilter("error", SAWarning)
        engine = original.get_engine(sys.argv[1])
        original.init_db(engine)
        with Session(engine) as session:
            run = original.RunLog(kind="test")
            session.add(run)
            session.commit()
            session.refresh(run)
            session.add(original.RunLogStep(
                run_log_id=run.id, iteration=1, created_at=datetime.now(UTC)
            ))
            session.commit()

        del sys.modules["sprout.db"]
        reimported = importlib.import_module("sprout.db")
        reimported.init_db(engine)
        with Session(engine) as session:
            assert session.exec(select(reimported.RunLogStep)).one().iteration == 1
            assert session.exec(select(original.RunLogStep)).one().iteration == 1

        importlib.reload(reimported)
        reimported.init_db(engine)
        with Session(engine) as session:
            assert session.exec(select(reimported.RunLogStep)).one().iteration == 1
    """)
    subprocess.run([sys.executable, "-c", code, str(tmp_path / "test.db")], check=True)
