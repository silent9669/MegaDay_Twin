# Thin wrapper: every target calls tasks.py so Windows (no make) and Linux run
# exactly the same commands. Logic lives in tasks.py, not here.
TASK = uv run python tasks.py

.PHONY: install lint fmt typecheck test check migrate db-reset dev serve
install:   ; uv sync
lint:      ; $(TASK) lint
fmt:       ; $(TASK) fmt
typecheck: ; $(TASK) typecheck
test:      ; $(TASK) test
check:     ; $(TASK) check
migrate:   ; $(TASK) migrate
db-reset:  ; $(TASK) db-reset
dev:       ; $(TASK) dev
serve:     ; $(TASK) serve
