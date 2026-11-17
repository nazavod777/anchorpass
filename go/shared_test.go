package anchorpass

import "testing"

func TestCustomTrustPoolsKeepClientLocalUpdates(t *testing.T) {
	_, rootsA := testTLS(t)
	_, rootsB := testTLS(t)
	first := testClient(t, "", WithRootCAs(rootsA))
	matching := testClient(t, "", WithRootCAs(rootsA))
	different := testClient(t, "", WithRootCAs(rootsB))
	if first.bundleState == matching.bundleState {
		t.Fatal("custom target roots shared update state across clients")
	}
	if first.bundleState == different.bundleState {
		t.Fatal("different CA keys with matching subject names shared update state")
	}
}

func TestSystemRootsShareUpdateState(t *testing.T) {
	first := testClient(t, "")
	matching := testClient(t, "")
	if first.bundleState != matching.bundleState {
		t.Fatal("default system roots stopped sharing updates")
	}
}
func TestCustomProxyRootsKeepClientLocalUpdates(t *testing.T) {
	_, roots := testTLS(t)
	first := testClient(t, "", WithProxyRootCAs(roots))
	matching := testClient(t, "", WithProxyRootCAs(roots))
	if first.bundleState == matching.bundleState {
		t.Fatal("custom proxy roots shared update state across clients")
	}
}
func TestCustomPoolRegistryLimitAndRelease(t *testing.T) {
	_, roots := testTLS(t)
	clients := make([]*Client, 0, maxUpdateStates)
	defer func() {
		for _, client := range clients {
			_ = client.Close()
		}
	}()
	for index := 0; index < maxUpdateStates; index++ {
		client, err := NewClient(WithRootCAs(roots))
		if err != nil {
			t.Fatal(err)
		}
		clients = append(clients, client)
	}
	_, err := NewClient(WithRootCAs(roots))
	requireCode(t, err, ResourceLimit)
	_ = clients[0].Close()
	replacement, err := NewClient(WithRootCAs(roots))
	if err != nil {
		t.Fatal(err)
	}
	_ = replacement.Close()
}
