package redisops

import (
	"context"
	"os"
)

func (c *Client) EvalLuaFromFile(ctx context.Context, filepath string, keys []string, args ...interface{}) (interface{}, error) {
	script, err := os.ReadFile(filepath)
	if err != nil {
		return nil, err
	}
	return c.rdb.Eval(ctx, string(script), keys, args...).Result()
}
