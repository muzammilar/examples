package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"strings"

	kjson "github.com/knadh/koanf/parsers/json"
	kyaml "github.com/knadh/koanf/parsers/yaml"
	kenv "github.com/knadh/koanf/providers/env"
	kfile "github.com/knadh/koanf/providers/file"
	"github.com/knadh/koanf/v2"
)

var envVarPrefix string

// https://docs.github.com/en/copilot/using-github-copilot/getting-started-with-github-copilot

// Struct to hold the configuration loader
type ConfigFileLoader struct {
	FilePath   string
	ParserFunc koanf.Parser
}

// Struct to hold the configuration
type Configuration struct {
	Ghardelli    string `koanf:"ghardelli" yaml:"ghardelli" json:"ghardelli"`
	Kinder       string
	Hersheys     string `koanf:"hersheys" yaml:"hersheys" json:"hersheys"`
	KitKat       kitKat `koanf:"kit_kat" yaml:"kit_kat" json:"kit_kat"`
	Lindt        Lindt
	MarsInc      MarsInc      `koanf:"mars_inc" yaml:"mars_inc" json:"mars_inc"`
	FerreroGroup FerreroGroup `koanf:"ferrero_group" yaml:"ferrero_group" json:"ferrero_group"`
}

type kitKat struct {
	Flavor string `koanf:"flavor" yaml:"flavor" json:"flavor"`
}

type Lindt struct {
	Lindor string `koanf:"lindor" yaml:"lindor" json:"lindor"`
}

type MarsInc struct {
	SnickersChocolate string `koanf:"snickers_chocolate" yaml:"snickers_chocolate" json:"snickers_chocolate"`
	Mars              string `koanf:"mars" yaml:"mars" json:"mars"`
}

type FerreroGroup struct {
	FerreroRocher FerreroRocher `koanf:"ferrero_rocher" yaml:"ferrero_rocher" json:"ferrero_rocher"`
}

type FerreroRocher struct {
	Raffaello string `koanf:"raffaello" yaml:"raffaello" json:"raffaello"`
}

func main() {
	// flags
	envPrefixPtr := flag.String("env-prefix", "MYAPP_", "The prefix of the environmental variables to override the local configuration optoins.") // skip validation
	baseConfPathPtr := flag.String("conf-base", "/config/config.yaml", "The path of the base configuration file.")                                // skip path validation
	overrideConfPathPtr := flag.String("conf-override", "/config/override.json", "The path of the override configuration file.")                  // skip path validation

	//parse flags
	flag.Parse()

	// set the env var prefix
	envVarPrefix = *envPrefixPtr

	// combine configuration paths
	configurations := []ConfigFileLoader{
		{FilePath: *baseConfPathPtr, ParserFunc: kyaml.Parser()},
		{FilePath: *overrideConfPathPtr, ParserFunc: kjson.Parser()},
	}

	// create a new configuration
	config, err := loadConfig(*envPrefixPtr, configurations...)
	if err != nil {
		panic(err) // panic is bad practice but we are just demonstrating the error handling
	}

	// print the configuration as json
	b, jerr := json.MarshalIndent(config, "", "  ")
	if jerr != nil {
		fmt.Println(err)
	}
	fmt.Print(string(b))

}

func getVarFromEnvName(s string) string {
	// For example, env vars: MYVAR__TYPE and MYVAR__PARENT1__CHILD1__NAME
	// will be merged into the "type" and the nested "parent1.child1.name"
	// keys in the config file here as we lowercase the key,
	// replace `__` with `.` and strip the MYVAR_ prefix so that
	// only "parent1.child1.name" remains.

	// read environmental variable and remove the prefix (and convert them to lower)
	s = strings.TrimPrefix(s, envVarPrefix)
	s = strings.ToLower(s)
	// convert all `__` to a `.` so that nested structs could be loaded via env variables
	// This allows something like MYAPP_ROGER_FED__RAFAEL to be converted to roger_fed.rafael
	koanfOption := strings.ReplaceAll(s, "__", ".")
	return koanfOption
}

func loadConfig(envVarPrefix string, confFileLoaders ...ConfigFileLoader) (*Configuration, error) {
	// Load the configuration
	var config Configuration

	// Global koanf instance. Use "." as the key path delimiter. This can be "/" or any character
	var k = koanf.New(".")
	// iterate over all configuration loaders
	for _, confLoader := range confFileLoaders {
		if err := k.Load(kfile.Provider(confLoader.FilePath), confLoader.ParserFunc); err != nil {
			return nil, err
		}
	}

	// Load values from environment variables MYAPP_*
	// For example, MYAPP_POSTGRES_USER will replace postgres_user loaded from config file
	// Load environment variables and merge into the loaded config.
	// "MYAPP" is the prefix to filter the env vars by.
	// "." is the delimiter used to represent the key hierarchy in env vars.
	// The (optional, or can be nil) function can be used to transform
	// the env var names, for instance, to lowercase them.
	_ = k.Load(kenv.Provider(envVarPrefix, ".", getVarFromEnvName), nil)

	// Note the Koanf allows you to have nested configs, but we are not using that here.
	// If you do use nested, use: k.UnmarshalWithConf("", &config, koanf.UnmarshalConf{Tag: "koanf", FlatPaths: true})
	if err := k.Unmarshal("", &config); err != nil {
		return nil, err
	}

	if err := validateConfig(&config); err != nil {
		return nil, err
	}

	return &config, nil
}

func validateConfig(config *Configuration) error {
	// validate the configuration
	// since it's a POC we can skip the validation
	return nil

}
