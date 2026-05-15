"""Multi-group context store for AgentRunner.

Wraps per-group GroupContext instances so that AgentRunner can call
get_history/add/clear with a group_id argument on a single shared object,
rather than fetching a GroupContext handle itself.
"""

from app.agent.context import GroupContext


class ContextStore:
    """Registry of GroupContext objects, keyed by group_id.

    Creates a new GroupContext on first access for an unknown group.
    """

    def __init__(self) -> None:
        self._contexts: dict[str, GroupContext] = {}

    def _get_or_create(self, group_id: str) -> GroupContext:
        if group_id not in self._contexts:
            self._contexts[group_id] = GroupContext(group_id)
        return self._contexts[group_id]

    def get_history(
        self,
        group_id: str,
        max_pairs: int | None = None,
        idle_minutes: int | None = None,
    ) -> list:
        """Return the conversation history for *group_id*.

        Args:
            group_id: The group JID.
            max_pairs: Forward to GroupContext.get_history; caps the number of
                user+assistant pairs returned.
            idle_minutes: Forward to GroupContext.get_history; overrides the
                module-level idle threshold.
        """
        return self._get_or_create(group_id).get_history(
            max_pairs=max_pairs,
            idle_minutes=idle_minutes,
        )

    def add(
        self,
        group_id: str,
        role: str,
        content: str,
        max_pairs: int | None = None,
    ) -> None:
        """Append a message to the history for *group_id*.

        Args:
            group_id: The group JID.
            role: ``"user"`` or ``"assistant"``.
            content: Message content (string or list of content blocks).
            max_pairs: Forward to GroupContext.add; overrides the module-level
                MAX_TURNS trim limit.
        """
        self._get_or_create(group_id).add(
            role=role,
            content=content,
            max_pairs=max_pairs,
        )

    def clear(self, group_id: str) -> None:
        """Remove the cached GroupContext for *group_id*.

        Call this when a group is rebound to a new blueprint so the next
        access loads a fresh context from the DB.
        """
        if group_id in self._contexts:
            del self._contexts[group_id]
