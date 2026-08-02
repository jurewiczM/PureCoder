import os


def parse_ports(ports_str):
    ports = set()
    for port_str in ports_str.split(','):
        if not port_str.isdigit():
            raise ValueError(f"Invalid port number: {port_str}")
        port = int(port_str)
        if 1 <= port <= 65535:
            ports.add(port)
    return sorted(list(ports))

def main():
    ports_str = os.getenv('PORTS')
    if not ports_str:
        print("No PORTS environment variable provided")
        exit(1)

    try:
        ports = parse_ports(ports_str)
        for port in ports:
            print(port)
    except Exception as e:
        print(f"Error parsing PORTS: {e}")
        exit(1)
