# Disaster Recovery Guide

On restart, preserve the emergency control state, restore the active registry model, and run read-only reconciliation of MT5 positions/orders/deals against local trade memory. Review any `STATE_MISMATCH` alert before allowing new orders.

Emergency Stop blocks new orders and preserves audit logs. The default `LEAVE_UNCHANGED` position policy avoids an unreviewed forced close. Reactivation requires a configured operator and `controlled_operator_reactivation` procedure. Roll back only to a previously approved immutable artifact.
