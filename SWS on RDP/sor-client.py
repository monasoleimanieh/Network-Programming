import socket
import select
import sys
import re
import datetime

# Create a non-blocking UDP socket
def create_socket(server_address):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    #sock.setblocking(0)
    
    #try:
    #    sock.bind(server_address)
    #except:
    #    print("Could not bind")
    #    sys.exit()

    return sock

# Parse command line arguments
def parse_command():
    # Check if correct num of arguments are entered
    if len(sys.argv) < 6 or (len(sys.argv) - 5) % 2 != 0:
        print("Usage: python3 sor-client.py <server_ip> <server_port> <client_buffer_size> <client_payload_length> <file1> <output1>")
        sys.exit()

    server = (sys.argv[1], int(sys.argv[2]))
    client_buffer_size = int(sys.argv[3])
    client_payload_length = int(sys.argv[4])
    requested_files = [sys.argv[i] for i in range(5, len(sys.argv), 2)]
    write_to_files = {sys.argv[i]: sys.argv[i + 1] for i in range(5, len(sys.argv), 2)}

    return server, client_buffer_size, client_payload_length, requested_files, write_to_files

def main():
    try:
        # Initialize variables
        recv_window = 1
        recv_ack = 0
        recv_cmd = ''
        recv_ackWin = []
        recv_seqLen = []

        seq_num = 0
        packet_ack_num = -1
        connection_state = 0
        connection_closed = False
        buffer_size = 1024
        commands_list = []
        receiver_acked = {}
        unacked_packet = {}
        unacked_seq = []

        # Parse command line arguments
        server, client_buffer_size, client_payload_length, requested_files, write_to_files = parse_command()
        # Create the socket
        sock = create_socket(server)
        inputs = [sock]
        outputs = [sock]
        err = []

        # Initialize variables
        requested_files = [sys.argv[i] for i in range(5, len(sys.argv), 2)]
        write_to_files = {sys.argv[i]: sys.argv[i + 1] for i in range(5, len(sys.argv), 2)}
        num_requested_files = len(requested_files)
        writing_state = 0
        current_file = ''
        file_length = 0
        curr_len = 0

        # Process received data + handle writing to files
        def process(payload, writing_state, current_file, file_length, curr_len, requested_files, write_to_files, num_requested_files):
            arr = re.split(r'\n', payload)
            if len(arr) == 0:
                return writing_state, current_file, file_length, curr_len
            # Process each line in the payload
            while len(arr) >= 1:
                # If writing state is 0, parse HTTP response header
                if(writing_state == 0):
                    if(re.search('HTTP/1.0 200 OK', payload)):
                        writing_state = 1
                        filename = requested_files.pop(0)
                        f = write_to_files[filename]
                        current_file = open(f, 'a')
                        arr.pop(0)
                        content_len = re.findall('\d.*', arr.pop(0))
                        file_length = int(content_len[0])
                        if(num_requested_files >= 2):
                            arr.pop(0)
                        arr.pop(0)
                    else:
                        filename = requested_files.pop(0)
                        f = write_to_files[filename]
                        arr.pop(0)
                        if(num_requested_files == 0):
                            arr.pop(0)
                        arr.pop(0)
                        arr.pop(0)
                # If writing state is 1, write data to file
                else:
                    while(file_length > curr_len and len(arr) >= 1):
                        data = arr.pop(0)
                        curr_len += len(data)
                        if(file_length > curr_len and len(arr) >= 1):
                            data += '\n'
                            curr_len += 1
                        current_file.write(data)
                    # If it finished writing file, reset writing state & close file
                    if(curr_len == file_length or curr_len > file_length):
                        writing_state = 0
                        current_file.close()
            return writing_state, current_file, file_length, curr_len

        # Process packet headers + payloads, and create packets
        def process_packet(string, is_sending=False, seq_num=None, packet_ack_num=None, buffer_size=None):
            recv_ackWin = []
            recv_seqLen = []
            recv_cmd = ''

            if is_sending:
                # Create packet header for sending
                cmd = string
                payload = ''
                header1 = f"Sequence: {seq_num}\r\nLength: {len(payload)}\r\n" if re.search("SYN|DAT|FIN", cmd) else ''
                header2 = f"Acknowledgement: {packet_ack_num}\r\nWindow: {buffer_size}\r\n" if re.search("ACK", cmd) else ''
                packet = f"{cmd}\r\n{header1}{header2}\r\n{payload}"
                return packet

            else:
                arr = string.splitlines()
                end_of_header = False
                cmd = ''
                for line in arr:
                    command_patterns = {
                        "SYN": re.compile(r"SYN"),
                        "DAT": re.compile(r"DAT"),
                        "ACK": re.compile(r"ACK"),
                        "FIN": re.compile(r"FIN"),
                    }

                    matched_command = None
                    for command, pattern in command_patterns.items():
                        if pattern.search(line):
                            matched_command = command
                            break

                if matched_command:
                    end_of_header = False
                    cmd_list = re.findall("SYN|DAT|ACK|FIN", line)
                    cmd = '|'.join(cmd_list)
                    recv_cmd = cmd
                else:
                    if re.search("Ackno|Window", line):
                        recv_ackWin.append(line)
                    elif re.search('Seq|Len', line) or not end_of_header:
                        recv_seqLen.append(line)
                        end_of_header = True
                return recv_cmd, recv_ackWin, recv_seqLen
            
        # Create payload from requested_files    
        def get_payload(l):
            global connection_state
            #global requested_files
            payload = ""
            if len(requested_files) == 0:
                return
            while(requested_files and l > len(payload) + 50):
                filename = requested_files.pop(0)
                payload += 'GET /' + filename + ' HTTP/1.0\r\n'
                if requested_files:
                    payload += 'Connection: keep-alive\r\n\r\n'
                else:
                    payload += '\r\n'

            if not requested_files:
                connection_state = 2
                
            return payload

        # Format log string
        def format_log_string(timestamp, state, cmd, headers):
            log_str = f"{timestamp}; {state}; {cmd}"
            for header, value in headers.items():
                log_str += f"; {header}: {value}"
            return log_str

        # Print formatted log string
        def print_log(state, cmd, v1, v2, v3, v4):
            fmt = "%a %b %d %H:%M:%S %Z %Y"
            now = datetime.datetime.now()
            local_time = now.astimezone()
            t = local_time.strftime(fmt)

            headers = {}
            if re.search("SYN|DAT|FIN", cmd):
                headers["Sequence"] = v1
                headers["Length"] = v2
            if re.search("ACK", cmd):
                headers["Acknowledge"] = v3
                headers["Window"] = v4

            log = format_log_string(t, state, cmd, headers)
            print(log)

        def send_request(server, sock, requested_files):
            for filename in requested_files:
                request = f"GET {filename} HTTP/1.0\r\nHost: {server[0]}\r\n\r\n"
                sock.sendto(request.encode(), server)
                
        send_request(server, sock, requested_files)

        while True:
            readable, writable, exceptional = select.select(inputs, outputs, err, 2)

            # Process writable sockets
            for s in writable:
                sending = []
                # Create packets to send
                while recv_ack + recv_window > seq_num and connection_state != 2:
                    cmd = ''
                    payload = ''
                    length = 0
                    if(connection_state == 0):
                        commands_list.append('SYN')
                    if requested_files:
                        payload = get_payload(client_payload_length)
                        length = len(payload)
                        commands_list.append('DAT')

                    commands_list.append('ACK')

                    if not requested_files:
                        commands_list.append('FIN')
                        connection_state = 2

                    while commands_list:
                        cmd += commands_list.pop(0)
                        if commands_list:
                            cmd += '|'
                    packet = process_packet(cmd, is_sending=True, seq_num=seq_num, packet_ack_num=packet_ack_num, buffer_size=buffer_size)

                    unacked_seq.append(seq_num)
                    unacked_packet[seq_num] = packet

                    sending.append(packet)
                    print_log('Send', cmd, seq_num, length, packet_ack_num, buffer_size)

                    # Update sequence + acknowledge numbers
                    if length == 0:
                        seq_num += 1
                    else:
                        seq_num += length + 1 if re.search("SYN", cmd) else length
                        if re.search("SYN", cmd):
                            packet_ack_num += 1

                # Send packets
                while sending:
                    p = sending.pop(0)
                    s.sendto(p.encode(), server)
                outputs.remove(s)

            # Process readable sockets
            for s in readable:
                encoded_packet = s.recv(buffer_size)
                packet = encoded_packet.decode()

                if re.search("RST", packet):
                    fmt = "%a %b %d %H:%M:%S %Z %Y"
                    now = datetime.datetime.now()
                    local_time = now.astimezone()
                    t = local_time.strftime(fmt)
                    s.close()
                    exit(0)

                ACK_flags = []
                respond_ACK = []
                receiver_out = []

                process_packet(packet)
                cmd = recv_cmd
                recv_seq = ""
                datalen = 0

                # Process received packets
                while recv_seqLen:
                    if not (re.search("SYN|DAT|FIN", cmd)):
                        recv_seqLen.pop(0)
                        recv_seqLen.pop(0)
                        break
                    head1 = recv_seqLen.pop(0)
                    head1 = head1.split()
                    recv_seq = int(head1[1])

                    head2 = recv_seqLen.pop(0)
                    head2 = head2.split()
                    datalen = int(head2[1])

                    recv_seqLen.pop(0)
                    if not (re.search("DAT", cmd)):
                        recv_seqLen.pop(0)

                    if(re.search('FIN', cmd)):
                        connection_closed = True

                    if(re.search("DAT", cmd) and datalen >= 1):
                        payload = ""
                        while(recv_seqLen and datalen > len(payload)):
                            payload += recv_seqLen.pop(0)
                            if(datalen > len(payload)):
                                payload += '\n'     
                                if(len(payload) == datalen):
                                    recv_seqLen.pop(0)
                    
                    if(recv_seq != packet_ack_num):
                        receiver_acked[recv_seq] = payload
                        ACK_flags.append('ACK')
                    elif(packet_ack_num > recv_seq):
                        pass
                    else:
                        receiver_acked[recv_seq] = payload

                        while recv_seq == packet_ack_num:
                            payload = receiver_acked[recv_seq]
                            writing_state, current_file, file_length, curr_len = process(payload, writing_state, current_file, file_length, curr_len, requested_files, write_to_files, num_requested_files)

                            packet_ack_num += 1 if datalen == 0 else len(payload)
                            if re.search("SYN", cmd):
                                packet_ack_num += 1

                            recv_seq = receiver_acked.get(packet_ack_num, recv_seq)

                            buffer_size -= len(payload)
                            if buffer_size == 0:
                                buffer_size = client_buffer_size

                            packet = process_packet('ACK', is_sending=True, seq_num=None, packet_ack_num=packet_ack_num, buffer_size=buffer_size)
                            receiver_out.append(packet)
                            ACK_flags.append('ACK')
                            respond_ACK.extend([packet_ack_num, buffer_size])

                while recv_ackWin:
                    head1 = recv_ackWin.pop(0)
                    head1 = head1.split()
                    recv_ack = int(head1[1])
                    head2 = recv_ackWin.pop(0)
                    head2 = head2.split()
                    recv_window = int(head2[1])

                    if(recv_ack >= 1 and connection_state != 2):
                        connection_state = 1

                print_log('Receive', cmd, recv_seq, datalen, recv_ack, recv_window)

                # Send ACKs for received packets
                while ACK_flags:
                    fmt = "%a %b %d %H:%M:%S %Z %Y"
                    now = datetime.datetime.now()
                    local_time = now.astimezone()
                    t = local_time.strftime(fmt)

                    cmd = ACK_flags.pop(0)
                    ack = str(respond_ACK.pop(0))
                    window = str(respond_ACK.pop(0))
                    p = receiver_out.pop(0)
                    s.sendto(p.encode(), server)
                    log = t + '; Send; ' + cmd + '; Acknowledge: ' + ack + '; Window: ' + window
                    print(log)

                outputs.append(s)

    # Handle keyboard interruption by user
    except KeyboardInterrupt:
        print("Program terminated by user")
        sys.exit()

    #finally:
        #sock.close()

if __name__ == "__main__":
    main()

