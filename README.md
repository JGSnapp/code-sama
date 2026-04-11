# code-sama

MVP-реализация системы из `Code-sama-architecture.md`:

- Scene Host с единым структурированным state + typed event bus.
- Agent 1 (режиссёр) и Agent 2 (исполнитель) с execution through episodes.
- Окна: VS-nya, Ji-nya, NYA-Paint, Browser.
- Аватар-controller с абстракцией VRM-адаптера.
- Аудио-подсистема (SFX events).
- SQLite schema + in-memory repository для event log.

## Запуск

```bash
npm install
npm run check
```

## Где смотреть

- Точка входа рантайма: `src/runtime/orchestrator.ts`
- Scene/state/event bus: `src/core/*`
- Агенты: `src/agents/*`
- Окна: `src/windows/windows.ts`
- Аватар: `src/avatar/avatarController.ts`
- Хранилище и SQLite schema: `src/storage/sqliteRepository.ts`
- Проверки соответствия требованиям: `tests/architecture.spec.ts`
