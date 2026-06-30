from src import llm, config, agents, trace
from src.agent import answer


def main():
    client = llm.get_client()
    multi = config.ONCALL_MODE == "multi"
    print(f"On-Call Copilot  (provider={config.PROVIDER}, mode={config.ONCALL_MODE})  "
          "— ask a question, Ctrl-C to quit.\n")
    while True:
        try:
            q = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue
        # log every run's full trajectory to logs/run-<id>.jsonl (observability)
        run_id = trace.new_run_id()
        logger = trace.file_logger(run_id, q, config.PROVIDER, config.ONCALL_MODE)
        try:
            if multi:                              # triage -> investigate -> verify -> postmortem
                result = agents.run(q, client, on_event=logger)
                print("\nbot>", result["answer"])
                if result["postmortem"]:
                    print("\n--- postmortem ---\n" + result["postmortem"])
            else:
                print("\nbot>", answer(q, client, on_event=logger))
            print(f"   (trace: {logger.path})\n")
        finally:
            logger.close()


if __name__ == "__main__":
    main()
