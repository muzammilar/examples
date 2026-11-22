package main

import (
	"strings"
	"testing"
)

func TestQualifiedExtensionsRequireExactNativeEngineBeforeInstallation(t *testing.T) {
	for _, pair := range [][2]string{{"v2.0.1", "v1.5.5"}, {"v2.0.0", "v2.0.1"}, {"v2.0.0", "v2.0.0"}, {"v2.1.0", "v2.1.0"}} {
		if _, err := qualifiedExtensionPins(pair[0], pair[1]); err == nil {
			t.Fatalf("unqualified native/expected pair accepted: %v", pair)
		}
	}
}

func TestQualifiedExtensionsKeepBothPinSetsSeparate(t *testing.T) {
	old, err := qualifiedExtensionPins("v1.5.5", "v1.5.5")
	if err != nil || len(old) != 3 || old[0].Version != "f9d5902" || old[1].Version != "827222f" || old[2].Version != "45163a28" {
		t.Fatalf("old pins changed: %+v %v", old, err)
	}
	newPins, err := qualifiedExtensionPins("v2.0.1", "v2.0.1")
	if err != nil || len(newPins) != 3 || newPins[0].Version != "4176191c96" || newPins[1].Version != "f71cfdb4bf" || newPins[2].Version != "f979b5b28a" {
		t.Fatalf("wrong 2.0.1 pins: %+v %v", newPins, err)
	}
	for _, pin := range newPins {
		if len(pin.SHA256) != 64 {
			t.Fatal("2.0.1 binary hash missing")
		}
	}
	if err = checkQualifiedExtensions(old, old); err != nil {
		t.Fatal(err)
	}
	if err = checkQualifiedExtensions(newPins, newPins); err != nil {
		t.Fatal(err)
	}
	if err = checkQualifiedExtensions(newPins, old); err == nil {
		t.Fatal("old extensions accepted for 2.0.1")
	}
}

func TestQualifiedExtensionsRejectWrongVersionHashAndSet(t *testing.T) {
	pins, _ := qualifiedExtensionPins("v2.0.1", "v2.0.1")
	for _, mutate := range []func([]qualifiedExtension) []qualifiedExtension{
		func(v []qualifiedExtension) []qualifiedExtension { v[0].Version = "unqualified"; return v },
		func(v []qualifiedExtension) []qualifiedExtension { v[0].SHA256 = strings.Repeat("0", 64); return v },
		func(v []qualifiedExtension) []qualifiedExtension { v[1] = v[0]; return v },
		func(v []qualifiedExtension) []qualifiedExtension { return v[:2] },
	} {
		if err := checkQualifiedExtensions(pins, mutate(append([]qualifiedExtension(nil), pins...))); err == nil {
			t.Fatal("unqualified extension set accepted")
		}
	}
}
