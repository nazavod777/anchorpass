use isahc::prelude::*;
use std::{
    io::{Read, Write},
    net::TcpListener,
    time::{Duration, Instant},
};
fn main() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        for connection in listener.incoming().take(2) {
            std::thread::spawn(move || {
                let mut stream = connection.unwrap();
                stream
                    .set_read_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                loop {
                    let mut request = Vec::new();
                    let mut byte = [0];
                    while !request.ends_with(b"\r\n\r\n") {
                        if stream.read_exact(&mut byte).is_err() {
                            return;
                        }
                        request.push(byte[0]);
                    }
                    if stream
                        .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
                        .is_err()
                    {
                        return;
                    }
                }
            });
        }
    });
    let url = format!("http://{addr}/");
    let rq = reqwest::blocking::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(5))
        .build()
        .unwrap();
    let ic = isahc::HttpClient::builder()
        .proxy(None)
        .timeout(Duration::from_secs(5))
        .build()
        .unwrap();
    println!("candidate,iteration,microseconds");
    for i in 0..101 {
        for name in if i % 2 == 0 {
            ["reqwest", "isahc"]
        } else {
            ["isahc", "reqwest"]
        } {
            let start = Instant::now();
            if name == "reqwest" {
                assert_eq!(rq.get(&url).send().unwrap().text().unwrap(), "ok");
            } else {
                assert_eq!(ic.get(&url).unwrap().text().unwrap(), "ok");
            }
            println!("{name},{i},{}", start.elapsed().as_micros());
        }
    }
    drop(rq);
    drop(ic);
    server.join().unwrap();
}
