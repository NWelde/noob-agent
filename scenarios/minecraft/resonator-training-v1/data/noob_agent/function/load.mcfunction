# Idempotent objective creation reports "already exists" on reload and is harmless.
scoreboard objectives add noob_agent.state dummy
scoreboard objectives add noob_agent.test dummy
function noob_agent:reset
