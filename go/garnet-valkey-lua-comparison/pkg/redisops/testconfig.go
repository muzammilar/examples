package redisops

// Container image constants for testing
const (
	ValkeyImage = "valkey/valkey:8.0"
	GarnetImage = "ghcr.io/microsoft/garnet:1.0"
	RedisPort   = "6379/tcp"
	// GarnetLuaCmd enables Lua scripting support in Garnet via command line flag.
	// Alternatively, you can use EnableLua in a Garnet config file.
	GarnetLuaCmd = "--lua"
)
