// Isolated MIoT camera on/off helper. Secrets enter over stdin, never argv/logs.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/AlexxIT/go2rtc/pkg/xiaomi"
	"gopkg.in/yaml.v3"
)

type input struct {
	Config     string `json:"config"`
	ConfigYAML string `json:"config_yaml"`
	Source     string `json:"source"`
	On         *bool  `json:"on"`
}
type property struct {
	IID    int      `json:"iid"`
	Type   string   `json:"type"`
	Format string   `json:"format"`
	Access []string `json:"access"`
}
type service struct {
	IID        int        `json:"iid"`
	Type       string     `json:"type"`
	Properties []property `json:"properties"`
}

var output = map[string]any{"success": false}

func publicJSON(endpoint string, value any) error {
	c := &http.Client{Timeout: 15 * time.Second}
	r, err := c.Get(endpoint)
	if err != nil {
		return fmt.Errorf("spec_unavailable")
	}
	defer r.Body.Close()
	if r.StatusCode != 200 {
		return fmt.Errorf("spec_unavailable")
	}
	return json.NewDecoder(io.LimitReader(r.Body, 16*1024*1024)).Decode(value)
}

func run() error {
	var in input
	if json.NewDecoder(io.LimitReader(os.Stdin, 32768)).Decode(&in) != nil {
		return fmt.Errorf("invalid_input")
	}
	u, err := url.Parse(in.Source)
	if err != nil || u.Scheme != "xiaomi" || u.User == nil {
		return fmt.Errorf("source_unavailable")
	}
	region, _ := u.User.Password()
	if !strings.Contains("|cn|de|i2|ru|sg|us|", "|"+region+"|") || region == "" {
		return fmt.Errorf("invalid_region")
	}
	did, model := u.Query().Get("did"), u.Query().Get("model")
	if did == "" || model == "" {
		return fmt.Errorf("device_unavailable")
	}
	output["model"], output["region"] = model, region
	var cfg struct {
		Xiaomi map[string]string `yaml:"xiaomi"`
	}
	b := []byte(in.ConfigYAML)
	if len(b) == 0 {
		b, err = os.ReadFile(in.Config)
	}
	if err != nil || yaml.Unmarshal(b, &cfg) != nil {
		return fmt.Errorf("session_unavailable")
	}
	token := cfg.Xiaomi[u.User.Username()]
	if token == "" {
		return fmt.Errorf("session_unavailable")
	}
	cloud := xiaomi.NewCloud("xiaomiio")
	if cloud.LoginWithToken(u.User.Username(), token) != nil {
		return fmt.Errorf("session_rejected")
	}
	base := "https://" + region + ".api.io.mi.com/app"
	if region == "cn" {
		base = "https://api.io.mi.com/app"
	}
	output["stage"] = "device_confirmation"
	res, err := cloud.Request(base, "/v2/home/device_list_page", "{}", nil)
	if err != nil {
		return fmt.Errorf("device_list_failed")
	}
	var devices struct {
		List []struct {
			DID   string `json:"did"`
			Model string `json:"model"`
		} `json:"list"`
	}
	if json.Unmarshal(res, &devices) != nil {
		return fmt.Errorf("device_list_failed")
	}
	matched := false
	for _, d := range devices.List {
		if d.DID == did && d.Model == model {
			matched = true
		}
	}
	if !matched {
		return fmt.Errorf("device_identity_mismatch")
	}
	output["stage"] = "spec_lookup"
	var registry struct {
		Instances []struct {
			Model string `json:"model"`
			Type  string `json:"type"`
		} `json:"instances"`
	}
	if publicJSON("https://miot-spec.org/miot-spec-v2/instances?status=all", &registry) != nil {
		return fmt.Errorf("spec_unavailable")
	}
	instance := ""
	for _, i := range registry.Instances {
		if i.Model == model {
			instance = i.Type
		}
	}
	if instance == "" {
		return fmt.Errorf("spec_unsupported")
	}
	var spec struct {
		Services []service `json:"services"`
	}
	if publicJSON("https://miot-spec.org/miot-spec-v2/instance?type="+url.QueryEscape(instance), &spec) != nil {
		return fmt.Errorf("spec_unavailable")
	}
	siid, piid := 0, 0
	for _, s := range spec.Services {
		if !strings.Contains(s.Type, ":service:camera-control:") {
			continue
		}
		for _, p := range s.Properties {
			if !strings.Contains(p.Type, ":property:on:") || p.Format != "bool" {
				continue
			}
			read, write := false, false
			for _, a := range p.Access {
				read = read || a == "read"
				write = write || a == "write"
			}
			if read && write {
				siid, piid = s.IID, p.IID
			}
		}
	}
	if siid == 0 {
		return fmt.Errorf("on_unsupported")
	}
	output["siid"], output["piid"] = siid, piid
	read := func() (bool, error) {
		data, _ := json.Marshal(map[string]any{"params": []any{map[string]any{"did": did, "siid": siid, "piid": piid}}})
		r, err := cloud.Request(base, "/miotspec/prop/get", string(data), nil)
		if err != nil {
			return false, fmt.Errorf("read_failed")
		}
		var rows []struct {
			DID   string `json:"did"`
			SIID  int    `json:"siid"`
			PIID  int    `json:"piid"`
			Code  int    `json:"code"`
			Value *bool  `json:"value"`
		}
		if json.Unmarshal(r, &rows) != nil || len(rows) != 1 {
			return false, fmt.Errorf("read_invalid")
		}
		p := rows[0]
		output["property_code"] = p.Code
		if p.DID != did || p.SIID != siid || p.PIID != piid || p.Code != 0 || p.Value == nil {
			return false, fmt.Errorf("read_rejected")
		}
		return *p.Value, nil
	}
	output["stage"] = "read_on"
	before, err := read()
	if err != nil {
		return err
	}
	output["on"] = before
	if in.On != nil && *in.On != before {
		output["stage"] = "write_on"
		data, _ := json.Marshal(map[string]any{"params": []any{map[string]any{"did": did, "siid": siid, "piid": piid, "value": *in.On}}})
		r, err := cloud.Request(base, "/miotspec/prop/set", string(data), nil)
		if err != nil {
			return fmt.Errorf("write_failed")
		}
		var rows []struct {
			DID  string `json:"did"`
			SIID int    `json:"siid"`
			PIID int    `json:"piid"`
			Code int    `json:"code"`
		}
		if json.Unmarshal(r, &rows) != nil || len(rows) != 1 || rows[0].DID != did || rows[0].SIID != siid || rows[0].PIID != piid || rows[0].Code != 0 {
			return fmt.Errorf("write_rejected")
		}
		time.Sleep(time.Second)
		after, err := read()
		if err != nil || after != *in.On {
			return fmt.Errorf("readback_mismatch")
		}
		output["on"] = after
	}
	output["success"], output["stage"] = true, "verified_property"
	return nil
}

func main() {
	if err := run(); err != nil {
		output["error_code"] = err.Error()
	}
	_ = json.NewEncoder(os.Stdout).Encode(output)
}
