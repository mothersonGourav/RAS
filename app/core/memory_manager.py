class MemoryManager:
    """Manages conversational memory to prevent token bloat."""

    @staticmethod
    def trim_history(messages: list, max_messages: int = 6) -> list:
        """
        Keeps only the most recent messages.
        max_messages=6 means the last 3 turns (User/Assistant pairs).
        """
        if not messages:
            return []
        trimmed = messages[-max_messages:]
        return trimmed