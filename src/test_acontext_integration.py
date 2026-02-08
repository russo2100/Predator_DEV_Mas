# src/test_acontext.py
# pyright: reportArgumentType=false
# pyright: reportCallIssue=false

import os
from acontext import AcontextClient

ACONTEXT_API_KEY = "sk-ac-SFZyxL7zcwo91pLM-ZrMOscAkoBS1ecKBChsj5Ewwq0"  # ← ТВОЙ КЛЮЧ

client = AcontextClient(api_key=ACONTEXT_API_KEY)
print("✅ Acontext Client initialized")

# 1. Создать сессию
session = client.sessions.create()
print(f"✅ Session created: {session.id}")

# 2. Сохранить mock-решения
client.sessions.store_message(
    session_id=session.id,
    blob={
        "role": "assistant",
        "content": "Cycle 1: BUY 5 lots @ 3.52 | RSI=45.5 | GWDD=0.85"
    },
    format="openai"
)
print("✅ Message 1 stored")

client.sessions.store_message(
    session_id=session.id,
    blob={
        "role": "assistant",
        "content": "Cycle 2: HOLD | RSI=47.2 | Price=3.54 | MIN_HOLD active"
    },
    format="openai"
)
print("✅ Message 2 stored")

# 3. Получить историю (ПРАВИЛЬНО)
messages = client.sessions.get_messages(
    session_id=session.id,
    format="openai"
)

print(f"\n📜 Retrieved messages:")
# Доступ через .items (список словарей)
for msg in messages.items:  # type: ignore
    print(f"  - {msg['content']}")

print(f"\n📊 Tokens used: {messages.this_time_tokens}")  # type: ignore
print(f"📊 Message IDs: {messages.ids}")  # type: ignore

# 4. Summary (пусто, т.к. мало данных)
summary = client.sessions.get_session_summary(session.id)
if summary:
    print(f"\n📝 Summary:\n{summary}")
else:
    print("\n📝 Summary: (empty - need more messages)")

print("\n🎉 Test completed successfully!")
