"""Domain logic for the audiobook pipeline.

No HTTP, no web framework, no model transport. Everything here is importable
by a service, by the corpus builder and by a script, which is why the rule
about what it may not depend on is worth keeping.
"""
