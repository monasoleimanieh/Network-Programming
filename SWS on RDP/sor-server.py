import socket
import select
import sys
import os
import re
import datetime

# Create a non-blocking UDP socket
def create_socket(server_address):
    #HOST = '' # receive on all interfaces
    #PORT = 5000
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.setblocking(0)
    #print(server)

    try:
        #server.bind((HOST, PORT))
        server.bind(server_address)
    except Exception as e:
        print(f"Could not bind: {e}")
        sys.exit()

    #print("binding successful")
    return server

# Parse command line arguments
def parse_command():
    # Check if correct num of arguments are entered
    if len(sys.argv) < 5:
        print("Usage: python3 sor-server.py <server_ip> <server_port> <server_buffer_size> <server_payload_length>")
        sys.exit()

    server_address = (sys.argv[1], int(sys.argv[2]))
    server_buffer_size = int(sys.argv[3])
    server_payload_length = int(sys.argv[4])

    return server_address, server_buffer_size, server_payload_length

def main():
    try:
        connected_addr = {}
        client_state = {}
        seq_num = {}
        ackno = {}
        seq_len_r = {}
        ack_r = {}
        respond_list = {}
        http_status = {}
        http_filename = {}
        http_respond = {}
        http_tmp_status = {}
        receiver_acked = {}
       
        # Parse command line arguments
        server_address, server_buffer_size, server_payload_length = parse_command()
        # Create the socket
        server = create_socket(server_address)
        inputs = [server]
        outputs = []

        # Process packets received + outgoing packets
        def process_packet(packet, addr, outgoing=False):
            if not outgoing:
                # Unpack received packet
                global ack_r
                global seq_len_r
                global recv_cmd

                fl = 0
                string = packet.decode()
                arr = string.splitlines()

                for line in arr:
                    cmd_match = re.search("(SYN|DAT|ACK|FIN)", line)
                    if cmd_match:
                        cmd_list = cmd_match.group(0).split('|')
                        cmd = '|'.join(cmd_list)
                        recv_cmd[addr] = cmd

                        if fl == 1 and not seq_len_r[addr].empty():
                            leftover = line.split(cmd)
                            seq_len_r[addr].append(leftover[0])

                        fl = 1 if "DAT" in cmd_list else 0
                    else:
                        header_match = re.search("(Ackno|Window)", line)
                        if header_match:
                            ack_r[addr].append(line)
                        else:
                            seq_len_r[addr].append(line)

            else:
                # Packetize outgoing packet
                cmd, a, b, c, d, payload = packet
                packet = f"{cmd}\n"
                if a != -1 and b != -1:
                    packet += f"Sequence: {a}\nLength: {b}\n"
                if c != -1 and d != -1:
                    packet += f"Acknowledge: {c}\nWindow: {d}\n"
                packet += f"\n{payload}"
                return packet.encode()

        # Get payload from the server response buffre
        def handle_payload(l, addr):
            global client_state
            global http_respond

            payload = ""
            while not http_respond[addr].empty() and l > len(payload):
                payload += http_respond[addr].get_nowait()

            client_state[addr] = 2 if http_respond[addr].empty() else client_state[addr]
            return payload

        # Update sequence num
        def update_seq_num(count, length, addr):
            global seq_num
            if(length == 0 or count >= 2):
                seq_num[addr] += 1
            else:
                seq_num[addr] += length

        # Handle file from client request
        def file_handler(line, q):
            try:
                filename = re.sub("/", "", line.split()[1])
                with open(filename) as f:
                    header = "HTTP/1.0 200 OK\nContent length: {}\n".format(os.path.getsize(filename))
                q.append(header)
                return filename
            except:
                q.append("HTTP/1.0 404 Not Found\n")
                return 0

        # Processes incoming HTTP requests and generates log
        def process(texts, addr):
            global respond_list
            global http_status
            global http_tmp_status
            global http_filename
            global http_respond

            format_base = "^GET\s/.*\sHTTP/1.0\r*\n"
            format_comp = format_base + "(" + "connection:\s*keep-alive\r*\n" + "|" + "connection:\s*close\r*\n" + ")"

            arr = texts.split("\n")[:-1]
            tmp_lists = [req + "\n" for req in arr]

            def format_time():
                fmt = "%a %b %d %H:%M:%S %Z %Y"
                now = datetime.datetime.now()
                local_time = now.astimezone()
                timestring = local_time.strftime(fmt)
                return timestring

            def handle_bad_request(request):
                respond_list[addr].append("HTTP/1.0 400 Bad Request\n")
                respond_list[addr].append(request)
                http_status[addr].append(0)
                http_filename[addr].append(0)

            def process_request(request_lists):
                while tmp_lists:
                    request = tmp_lists.pop(0)
                    request_lists.append(request)
                    if len(request_lists) == 1 and not re.search(format_base, request):
                        handle_bad_request(request)
                        break
                    if len(request_lists) >= 2:
                        process_multi_line_request(request_lists)
                        if not request_lists:
                            break

            def process_multi_line_request(request_lists):
                line1, line2 = request_lists[:2]
                request = line1 + line2.lower()

                if re.search(format_base + "\r*\n", request):
                    handle_single_line_request(line1, request)
                    request_lists.clear()
                elif re.search(format_comp, request):
                    handle_multi_line_request(request_lists)
                else:
                    handle_bad_request(request)
                    request_lists.clear()

            def handle_single_line_request(line1, request):
                filename = file_handler(line1, respond_list[addr])
                respond_list[addr].append(request)
                http_filename[addr].append(filename)
                http_status[addr].append(0)

            def handle_multi_line_request(request_lists):
                if len(request_lists) == 3:
                    line3 = request_lists.pop(2)
                    request = request_lists[0] + request_lists[1].lower() + line3

                    if re.search(format_comp + "\r*\n", request):
                        filename = file_handler(request, respond_list[addr])
                        http_filename[addr].append(filename)
                        http_status[addr].append(1 if re.search("alive", request) else 0)
                        http_tmp_status[addr] = 1
                        respond_list[addr].append(request)
                    else:
                        handle_bad_request(request)
                else:
                    request_lists.pop(0)

            request_lists = []
            process_request(request_lists)

            if http_tmp_status[addr] == 0:
                process_response()

            def process_response():
                while respond_list[addr]:
                    response = respond_list[addr].pop(0)
                    client_request = respond_list[addr].pop(0)
                    f = http_filename[addr].pop(0)

                    http_respond[addr].append(response)
                    status = http_status[addr].pop(0)
                    connection_status = (
                        'Connection: keep-alive\n\n' if status == 1
                        else 'Connection: close\n\n' if http_tmp_status[addr] == 1
                        else '\n'
                    )
                    http_respond[addr].append(connection_status)

                    if f != 0:
                        with open(f) as file_obj:
                            for line in file_obj:
                                for byte in line:
                                    http_respond[addr].append(byte)

                    t = format_time()
                    client_request = re.sub("\r*\n", " ", client_request)
                    log = f"{t}: {addr} {client_request}; {response}"
                    print(log)

        while True:
            readable, writable, exceptional = select.select(inputs, outputs, inputs, 7)
            for s in readable:
                packet, address = s.recvfrom(server_buffer_size)               
                if(address not in connected_addr):
                    connected_addr[address] = True
                    client_state[address] = 0
                    seq_num[address] = 0
                    ackno[address] = 0
                    seq_len_r[address] = []
                    ack_r[address] = []
                    respond_list[address] = []
                    http_status[address] = []
                    http_filename[address] = []
                    http_respond[address] = []
                    http_tmp_status[address] = 0
                    receiver_acked[address] = {}
                
                ACK_flags = []
                others_flags = []
                respond_ACK = []
                respond_DAT = []
                process_packet(packet, address)


                cmd = recv_cmd[address]
                while len(seq_len_r[address]) > 0:
                    if not (re.search("SYN|DAT|FIN", cmd)):
                        seq_len_r[address].pop(0)
                        seq_len_r[address].pop(0)
                        break
                    header_1 = seq_len_r[address].pop(0)
                    header_1 = header_1.split()
                    recv_seq = int(header_1[1])

                    header_2 = seq_len_r[address].pop(0)
                    header_2 = header_2.split()
                    datalen = int(header_2[1])

                    if(datalen >= server_buffer_size + 1):
                        s.sendto(b'RST', address)
                        client_state[address] = -1 
                        break

                    seq_len_r[address].pop(0)
                    if not (re.search("DAT", cmd)):
                        seq_len_r[address].pop(0)

                    if(re.search("DAT", cmd) and datalen >= 1):
                        payload = ""
                        while len(seq_len_r[address]) > 0 and datalen > len(payload):
                            payload += seq_len_r[address].pop(0)
                            if(datalen > len(payload)):
                                payload += '\n'     
                                if(len(payload) == datalen):
                                    seq_len_r[address].pop(0)

                    if ackno[address] > recv_seq:
                        pass
                    else:
                        receiver_acked[address][recv_seq] = payload
                        for recv_seq in receiver_acked[address]:
                            if recv_seq == ackno[address]:
                                payload = receiver_acked[address][recv_seq]
                                process(payload, address)
                                if datalen != 0 or re.search("SYN", cmd):
                                    ackno[address] += len(payload)

                                if ackno[address] in receiver_acked[address]:
                                    recv_seq = ackno[address]

                                buffer_size -= len(payload)
                                if buffer_size - len(payload) != 0:
                                    pass
                                else:
                                    buffer_size = server_buffer_size

                                ACK_flags.append('ACK')
                                respond_ACK.append(ackno[address])
                                respond_ACK.append(buffer_size)
                            else:
                                break

                if client_state[address] == -1:
                    for d in [client_state, ack_r, seq_len_r, recv_cmd, connected_addr]:
                        d.pop(address, None)
                    break

                recv_ack = 0
                recv_window = 1
                while len(ack_r[address]) > 0:
                    header_1 = ack_r[address].pop(0)
                    header_1 = header_1.split()
                    recv_ack = int(header_1[1])
                    header_2 = ack_r[address].pop(0)
                    header_2 = header_2.split()
                    recv_window = int(header_2[1])
                        
                    if(recv_ack >= 1 and client_state[address] != 2):
                        client_state[address] = 1
                        
                while recv_ack + recv_window > seq_num[address] and client_state[address] != 2:
                    cmd_flag = ''
                    payload = ''
                    length = 0

                    if client_state[address] == 0:
                        cmd_flag = 'SYN'
                        client_state[address] = 1

                    if recv_ack + recv_window > seq_num[address]:
                        cmd_flag = f"{cmd_flag}|DAT" if cmd_flag else "DAT"
                        payload = handle_payload(server_payload_length, address)
                        length = len(payload)

                    if client_state[address] == 2:
                        cmd_flag = f"{cmd_flag}|FIN" if cmd_flag else "FIN"

                    others_flags.append(cmd_flag)
                    respond_DAT.extend([seq_num[address], length, payload])
                    update_seq_num(len(cmd_flag.split('|')), length, address)

                sending_list = []
                while len(ACK_flags) > 0 or len(others_flags) > 0:
                    header_1, header_2, header_3, header_4 = -1, -1, -1, -1
                    cmd_line = ''
                    payload = ''
                    if len(ACK_flags) > 0:
                        cmd_line += ACK_flags.pop(0)
                        header_3 = respond_ACK.pop(0)
                        header_4 = respond_ACK.pop(0)
                        if len(others_flags) > 0:
                            cmd_line += '|'
                            cmd_line += others_flags.pop(0)
                            header_1 = respond_DAT.pop(0)
                            header_2 = respond_DAT.pop(0)
                            payload = respond_DAT.pop(0)
                    else:
                        cmd_line += others_flags.pop(0)
                        header_1 = respond_DAT.pop(0)
                        header_2 = respond_DAT.pop(0)
                        payload = respond_DAT.pop(0)
                            
                    packet = process_packet((cmd_line, header_1, header_2, header_3, header_4, payload), address, True)
                    obj = (packet, address)
                    sending_list.append(obj)
                outputs.append(s)            

            for s in writable:
                while len(sending_list) > 0:
                    p, addr = sending_list.pop(0)
                    s.sendto(p, addr)
                outputs.remove(s)

            if readable or writable:
                pass
            else:
                break

    # Handle keyboard interruption by user
    except KeyboardInterrupt:
        print("Program terminated by user")
        sys.exit()

if __name__ == "__main__":
    main()
