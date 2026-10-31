# Lệnh tiện ích ở gốc repo. Máy Windows không có make thì chạy thẳng lệnh node bên dưới.
.PHONY: dev dev-fixtures check
dev:          ; node scripts/dev.mjs
dev-fixtures: ; node scripts/dev.mjs --fixtures
check:
	node scripts/check.mjs
