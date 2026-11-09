// isp-picker phone agent: a minimal SOCKS5 (CONNECT only, no auth) proxy that
// runs on an Android phone as the adb shell user. The Mac helper reaches it
// through `adb forward`, so Chrome's traffic leaves through the phone's own
// internet without any network interface or routing change on the Mac.
//
// Build: GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -o build/agent-arm64 .
package main

import (
	"context"
	"encoding/binary"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"strings"
	"sync/atomic"
	"time"
)

var (
	listen      = flag.String("listen", "127.0.0.1:18080", "address to listen on (phone side)")
	dnsList     = flag.String("dns", "", "comma-separated DNS servers of the phone's network")
	dialTimeout = flag.Duration("timeout", 15*time.Second, "connect timeout")
	version     = flag.Bool("version", false, "print version and exit")
)

const agentVersion = "1.0.0"

var dnsIdx uint32

func resolver() *net.Resolver {
	servers := []string{}
	for _, s := range strings.Split(*dnsList, ",") {
		if s = strings.TrimSpace(s); s != "" {
			servers = append(servers, net.JoinHostPort(s, "53"))
		}
	}
	if len(servers) == 0 {
		return net.DefaultResolver
	}
	return &net.Resolver{
		PreferGo: true,
		Dial: func(ctx context.Context, network, _ string) (net.Conn, error) {
			d := net.Dialer{Timeout: 3 * time.Second}
			var last error
			start := atomic.AddUint32(&dnsIdx, 1)
			for i := range servers {
				c, err := d.DialContext(ctx, network, servers[(int(start)+i)%len(servers)])
				if err == nil {
					return c, nil
				}
				last = err
			}
			return nil, last
		},
	}
}

func reply(c net.Conn, code byte) {
	c.Write([]byte{5, code, 0, 1, 0, 0, 0, 0, 0, 0})
}

func handle(c net.Conn, r *net.Resolver) {
	defer c.Close()
	c.SetDeadline(time.Now().Add(20 * time.Second))
	buf := make([]byte, 262)
	if _, err := io.ReadFull(c, buf[:2]); err != nil || buf[0] != 5 {
		return
	}
	if _, err := io.ReadFull(c, buf[:buf[1]]); err != nil {
		return
	}
	c.Write([]byte{5, 0})
	if _, err := io.ReadFull(c, buf[:4]); err != nil {
		return
	}
	cmd, atyp := buf[1], buf[3]
	var host string
	switch atyp {
	case 1:
		io.ReadFull(c, buf[:4])
		host = net.IP(buf[:4]).String()
	case 3:
		io.ReadFull(c, buf[:1])
		n := int(buf[0])
		io.ReadFull(c, buf[:n])
		host = string(buf[:n])
	case 4:
		io.ReadFull(c, buf[:16])
		host = net.IP(buf[:16]).String()
	default:
		reply(c, 8)
		return
	}
	if _, err := io.ReadFull(c, buf[:2]); err != nil {
		return
	}
	port := binary.BigEndian.Uint16(buf[:2])
	if cmd != 1 {
		reply(c, 7)
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), *dialTimeout)
	defer cancel()
	addrs := []string{host}
	if net.ParseIP(host) == nil {
		ips, err := r.LookupIP(ctx, "ip4", host)
		if err != nil || len(ips) == 0 {
			if ips6, err6 := r.LookupIP(ctx, "ip6", host); err6 == nil && len(ips6) > 0 {
				ips = ips6
			} else {
				log.Printf("dns %s: %v", host, err)
				reply(c, 4)
				return
			}
		}
		addrs = addrs[:0]
		for _, ip := range ips {
			addrs = append(addrs, ip.String())
		}
	}
	var remote net.Conn
	var err error
	d := net.Dialer{}
	for _, a := range addrs {
		remote, err = d.DialContext(ctx, "tcp", net.JoinHostPort(a, fmt.Sprint(port)))
		if err == nil {
			break
		}
	}
	if err != nil {
		log.Printf("connect %s:%d: %v", host, port, err)
		var oe *net.OpError
		if errors.As(err, &oe) && strings.Contains(err.Error(), "refused") {
			reply(c, 5)
		} else {
			reply(c, 4)
		}
		return
	}
	defer remote.Close()
	if tc, ok := remote.(*net.TCPConn); ok {
		tc.SetNoDelay(true)
	}
	reply(c, 0)
	c.SetDeadline(time.Time{})

	done := make(chan struct{}, 2)
	pipe := func(dst, src net.Conn) {
		io.Copy(dst, src)
		if tc, ok := dst.(*net.TCPConn); ok {
			tc.CloseWrite()
		}
		done <- struct{}{}
	}
	go pipe(remote, c)
	go pipe(c, remote)
	<-done
	<-done
}

func main() {
	flag.Parse()
	if *version {
		fmt.Println(agentVersion)
		return
	}
	log.SetOutput(os.Stderr)
	ln, err := net.Listen("tcp", *listen)
	if err != nil {
		log.Fatal(err)
	}
	log.Printf("isp-picker agent %s on %s, dns=%q", agentVersion, *listen, *dnsList)
	r := resolver()
	for {
		c, err := ln.Accept()
		if err != nil {
			continue
		}
		go handle(c, r)
	}
}
