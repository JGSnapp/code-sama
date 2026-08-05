# Агенты code-sama

Два LangGraph-агента работают параллельно. Зритель слышит только **Streamer**; руки на рабочем столе — у **Worker**.

## Схема

```
  Пользователь
       │  чат
       ▼
  ┌─────────────┐   start_coding    ┌──────────────┐
  │  Streamer   │ ───────────────►  │     Bus      │
  │  (голос)    │ ◄── worker_done ──│  (EventBus)  │
  └─────────────┘                   └──────┬───────┘
       ▲                                   │ worker_task
       │ narration                         ▼
       │                            ┌──────────────┐
       └────── progress ────────────│    Worker    │
                                    │  (инструменты│
                                    │   ОС / apps) │
                                    └──────┬───────┘
                                           │
                                           ▼
                                    Win95 Desktop
```

## Роли

| Агент | Модель (роль) | Что делает |
|---|---|---|
| **Streamer** | `streamer` | Говорит с тобой, вызывает `start_coding` / `set_mood` / `narrate` |
| **Worker** | `worker` | Двигает мышь, печатает, открывает окна, пишет код |
| **Narration** | `narrator` | Короткие реплики поверх работы рук |

## Шина событий

- `worker_task` — Streamer → Worker (новая задача)
- `worker_done` — Worker → Streamer (итог, одна реакция без новых tool-вызовов)
- `interrupt_worker` — стоп текущей задачи
- `progress` — сырые события инструментов → пакетная narration

## Почему «одно сообщение и стоп»

Если LLM Streamer'а **не умеет tool calling** (например Codex без tools), она отвечает текстом и не вызывает `start_coding`. В code-sama-os есть **fallback**: если просьба похожа на задачу, Worker всё равно запускается. Смотри журнал действий — там будет `fallback → worker`.
