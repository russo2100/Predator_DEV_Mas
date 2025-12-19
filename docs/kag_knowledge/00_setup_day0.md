# KAG Node: Project Initialization (Day 0)
**Date:** 2025-11-22
**Project:** FinAgent v1 (Predator Arch)
**Environment:** Windows 11, Python 3.13

## Архитектурные Решения
1.  **Dependency Management**: Использование `venv` вместо Poetry на старте для упрощения работы с Windows-специфичными билдами Python 3.13 (JIT совместимость).
2.  **Path Safety**: Обнаружен риск кириллицы в пути (`Users\Руслан`). Решение: Использование `pathlib` во всем коде проекта для кросс-платформенной совместимости путей.
3.  **Core Stack Selection**:
    *   `pydantic v2`: Выбран как стандарт данных для обеспечения строгой типизации между агентами (Contract-first design).
    *   `tinkoff-investments`: Использование официального SDK вместо самописных оберток для снижения maintenance cost.
    *   `asyncio`: Принудительное использование асинхронного подхода (через `httpx` и SDK) для обеспечения высокой пропускной способности (High Frequency Data polling).

## Технический Долг (Day 0)
*   Отсутствует конфигурация линтеров (ruff/mypy) — добавить на Day 1.
*   Не настроен Git репозиторий — инициализировать перед первым коммитом кода.

## Следующий шаг
Реализация `src/core/connection.py`: Proof of Concept подключения к Sandbox Тинькофф API.
