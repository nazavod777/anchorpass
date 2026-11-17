module github.com/nazavod777/anchorpass/go/benchmarks

go 1.23.0

require github.com/go-resty/resty/v2 v2.16.5

require (
	github.com/nazavod777/anchorpass/go v0.0.0
	github.com/valyala/fasthttp v1.65.0
	golang.org/x/net v0.43.0
)

require (
	github.com/andybalholm/brotli v1.2.0 // indirect
	github.com/klauspost/compress v1.18.0 // indirect
	github.com/valyala/bytebufferpool v1.0.0 // indirect
)

replace github.com/nazavod777/anchorpass/go => ..
