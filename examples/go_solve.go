// Solve a reCAPTCHA v3 anchor URL with the anchorpass Go client.
//
// Copy this file into your application module (with the anchorpass
// dependency and, until publication, the local replace directive), then:
//
//	go run go_solve.go ANCHOR_URL [PROXY_URL]
//
// PROXY_URL is any absolute http/https/socks5/socks5h URL with an explicit
// host and port; omit it for a direct route. The action defaults to the
// URL's `sa`.
package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"time"

	anchorpass "github.com/nazavod777/anchorpass/go"
)

func main() {
	if len(os.Args) < 2 {
		log.Fatal("usage: go run go_solve.go ANCHOR_URL [PROXY_URL]")
	}
	anchorURL := os.Args[1]

	options := []anchorpass.Option{
		anchorpass.WithTimeout(30 * time.Second),
		anchorpass.WithMemoryCache(),
	}
	if len(os.Args) > 2 {
		options = append(options, anchorpass.WithProxy(os.Args[2]))
	}

	client, err := anchorpass.NewClient(options...)
	if err != nil {
		log.Fatal(err)
	}
	defer client.Close()

	result, err := client.Solve(context.Background(), anchorURL)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("client  token: %s…\n", truncate(result.Token, 16))

	// One-shot helper: creates and closes its own client.
	oneShot, err := anchorpass.Solve(context.Background(), anchorURL)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("one-shot token: %s…\n", truncate(oneShot.Token, 16))
	fmt.Println("verify the token server-side in your application")
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}
