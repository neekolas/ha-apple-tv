// The network socket and HAP credentials stay in Python. Use the release's
// mirroring handshake just as its sender does before the first control SETUP.
use std::io::{self, Read, Write};
use tvpipe_fairplay::FpSession;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut session = FpSession::new()?;
    let mut input = io::stdin().lock();
    let mut output = io::stdout().lock();
    output.write_all(&session.m1())?;
    output.flush()?;
    let mut m2 = [0; 142];
    input.read_exact(&mut m2)?;
    output.write_all(&session.m3(&m2)?)?;
    output.flush()?;
    let mut m4 = [0; 32];
    input.read_exact(&mut m4)?;
    session.check_m4(&m4)?;
    Ok(())
}
