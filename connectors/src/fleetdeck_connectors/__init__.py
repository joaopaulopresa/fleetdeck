"""Fleetdeck connectors: MCP servers that act as the person an agent works for.

A connector receives the agent's own token with every call. It never trusts anything the model
says about who is asking: the control plane resolves the token, decides whether that role may
use the tool on that record's case, and writes the decision to the audit trail.
"""
