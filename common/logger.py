from pathlib import Path
from datetime import datetime
import threading


lock = threading.Lock()


def current_time():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# Write one event to a log file
def log_event(log_path, event_type, description):
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with lock:
        created = current_time()
        old_events = ""

        # Keep old events and original creation time
        if path.exists():
            content = path.read_text(encoding="utf-8")

            for line in content.splitlines():
                if line.startswith("Creation Date/Time:"):
                    created = line.split(": ", 1)[1]
                    break

            if "--- EVENTS ---\n" in content:
                old_events = content.split("--- EVENTS ---\n", 1)[1]

        modified = current_time()

        event = (
            f"Event Type: {event_type}\n"
            f"Date/Time: {modified}\n"
            f"Description: {description}\n"
            f"{'-' * 50}\n"
        )

        with open(path, "w", encoding="utf-8") as file:
            file.write(f"File Name: {path.name}\n")
            file.write(f"Creation Date/Time: {created}\n")
            file.write(f"Last Modified Date/Time: {modified}\n")
            file.write("\n--- EVENTS ---\n")
            file.write(old_events)
            file.write(event)