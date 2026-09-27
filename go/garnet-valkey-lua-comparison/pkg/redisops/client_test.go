package redisops

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/docker/go-connections/nat"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"
)

// setupValkeyContainer creates and starts a Valkey container
func setupValkeyContainer(ctx context.Context) (testcontainers.Container, string, error) {
	req := testcontainers.ContainerRequest{
		Image:        ValkeyImage,
		ExposedPorts: []string{RedisPort},
		WaitingFor:   wait.ForLog("Ready to accept connections"),
	}

	container, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: req,
		Started:          true,
	})
	if err != nil {
		return nil, "", err
	}

	mappedPort, err := container.MappedPort(ctx, "6379")
	if err != nil {
		return nil, "", err
	}

	hostIP, err := container.Host(ctx)
	if err != nil {
		return nil, "", err
	}

	addr := fmt.Sprintf("%s:%s", hostIP, mappedPort.Port())
	return container, addr, nil
}

// setupGarnetContainer creates and starts a Garnet container with Lua scripting enabled
func setupGarnetContainer(ctx context.Context) (testcontainers.Container, string, error) {
	req := testcontainers.ContainerRequest{
		Image:        GarnetImage,
		ExposedPorts: []string{RedisPort},
		Cmd:          []string{GarnetLuaCmd},
		WaitingFor:   wait.ForListeningPort(nat.Port(RedisPort)),
	}

	container, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: req,
		Started:          true,
	})
	if err != nil {
		return nil, "", err
	}

	mappedPort, err := container.MappedPort(ctx, "6379")
	if err != nil {
		return nil, "", err
	}

	hostIP, err := container.Host(ctx)
	if err != nil {
		return nil, "", err
	}

	addr := fmt.Sprintf("%s:%s", hostIP, mappedPort.Port())
	return container, addr, nil
}

func TestValkey_BasicOperations(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	container, addr, err := setupValkeyContainer(ctx)
	require.NoError(t, err)
	defer container.Terminate(ctx)

	client := NewClient(addr)
	defer client.Close()

	// Test Ping
	err = client.Ping(ctx)
	assert.NoError(t, err)

	// Test Set and Get
	key := "test:valkey:key"
	value := "test-value"
	err = client.Set(ctx, key, value, 5*time.Minute)
	assert.NoError(t, err)

	retrieved, err := client.Get(ctx, key)
	assert.NoError(t, err)
	assert.Equal(t, value, retrieved)

	// Test Delete
	err = client.Del(ctx, key)
	assert.NoError(t, err)

	_, err = client.Get(ctx, key)
	assert.Error(t, err)
}

func TestGarnet_BasicOperations(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	container, addr, err := setupGarnetContainer(ctx)
	require.NoError(t, err)
	defer container.Terminate(ctx)

	client := NewClient(addr)
	defer client.Close()

	// Test Ping
	err = client.Ping(ctx)
	assert.NoError(t, err)

	// Test Set and Get
	key := "test:garnet:key"
	value := "test-value"
	err = client.Set(ctx, key, value, 5*time.Minute)
	assert.NoError(t, err)

	retrieved, err := client.Get(ctx, key)
	assert.NoError(t, err)
	assert.Equal(t, value, retrieved)

	// Test Delete
	err = client.Del(ctx, key)
	assert.NoError(t, err)

	_, err = client.Get(ctx, key)
	assert.Error(t, err)
}

func TestValkey_LuaScript(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	container, addr, err := setupValkeyContainer(ctx)
	require.NoError(t, err)
	defer container.Terminate(ctx)

	client := NewClient(addr)
	defer client.Close()

	// Set up test data
	key := "test:valkey:lua"
	value := "lua-test-value"
	err = client.Set(ctx, key, value, 5*time.Minute)
	require.NoError(t, err)

	// Test inline Lua script
	luaScript := `return redis.call('GET', KEYS[1])`
	result, err := client.EvalLua(ctx, luaScript, []string{key})
	assert.NoError(t, err)
	assert.Equal(t, value, result)

	// Test Lua script from file
	scriptPath := createTestLuaScript(t)
	defer os.Remove(scriptPath)

	fileResult, err := client.EvalLuaFromFile(ctx, scriptPath, []string{key})
	assert.NoError(t, err)
	assert.Contains(t, fileResult, "Value from Lua:")
	assert.Contains(t, fileResult, value)
}

func TestGarnet_LuaScript(t *testing.T) {
	t.Parallel()
	ctx := context.Background()

	container, addr, err := setupGarnetContainer(ctx)
	require.NoError(t, err)
	defer container.Terminate(ctx)

	client := NewClient(addr)
	defer client.Close()

	// Set up test data
	key := "test:garnet:lua"
	value := "lua-test-value"
	err = client.Set(ctx, key, value, 5*time.Minute)
	require.NoError(t, err)

	// Test inline Lua script
	luaScript := `return redis.call('GET', KEYS[1])`
	result, err := client.EvalLua(ctx, luaScript, []string{key})
	assert.NoError(t, err)
	assert.Equal(t, value, result)

	// Test Lua script from file
	scriptPath := createTestLuaScript(t)
	defer os.Remove(scriptPath)

	fileResult, err := client.EvalLuaFromFile(ctx, scriptPath, []string{key})
	assert.NoError(t, err)
	assert.Contains(t, fileResult, "Value from Lua:")
	assert.Contains(t, fileResult, value)
}

// createTestLuaScript creates a temporary Lua script file for testing
func createTestLuaScript(t *testing.T) string {
	tmpDir := t.TempDir()
	scriptPath := filepath.Join(tmpDir, "test.lua")

	scriptContent := `local key = KEYS[1]
local value = redis.call('GET', key)
if value then
    return 'Value from Lua: ' .. value
else
    return 'Key not found'
end`

	err := os.WriteFile(scriptPath, []byte(scriptContent), 0644)
	require.NoError(t, err)

	return scriptPath
}
