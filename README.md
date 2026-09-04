# MDoNER Service A — Memory Assistance Calling Companion

Independent development repository for the Service A voice call bot.

Scope:
- Twilio outbound calling
- Voice conversation
- Question planning
- Speech answer evaluation
- Safety handling
- CALL session/metric generation
- Mock testing

This repository is intended for development and testing before integration
into the main MDoNER project.

The developer outbound-call endpoint requires the `SERVICE_A_TRIGGER_AUTH_TOKEN`
environment variable and the same value in the `X-Service-A-Trigger-Token`
request header. Do not log or include this token in responses.