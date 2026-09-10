from widegold.settings.app import get_settings


def main() -> None:
    settings = get_settings()
    if settings.langgraph_checkpoint_mode != "postgres":
        print("LangGraph PostgreSQL checkpointing disabled; setup skipped.")
        return
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(settings.langgraph_database_url) as saver:
        saver.setup()
    print("LangGraph PostgreSQL checkpoint schema ready.")


if __name__ == "__main__":
    main()
