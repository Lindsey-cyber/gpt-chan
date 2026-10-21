"""Public error categories for local tool validation, without model output bodies."""
class ToolProtocolError(ValueError):
    REASONS = frozenset(('invalid_json','invalid_arguments','invalid_tool','multiple_tool_calls','repeated_status'))

    def __init__(self, reason):
        self.reason = reason if reason in self.REASONS else 'invalid_arguments'
        super().__init__(self.reason)
