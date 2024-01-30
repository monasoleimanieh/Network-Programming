import socket
import select
import sys
import re
import datetime

class Server:
    def __init__(self, ip_addr, port_number, read_file_name, write_file_name):
        # Create a socket + non-blocking
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(0)
        self.sock.bind((ip_addr, port_number))

        # Echo server
        self.h2 = ("10.10.1.100", 8888)

        # Handle files
        try:
            self.file_read = open(read_file_name, 'r')
            self.file_write = open(write_file_name, 'a')
            self.file_content = [letter for line in self.file_read for letter in line]
            self.file_read.close()
        except IOError:
            print('Could not open the file.')

        # Initialize variables
        self.inputs = [self.sock]
        self.outputs = [self.sock]
        self.error = []
        self.receiver_window = 1024 * 4
        self.sender_window = 1

        self.receiver_ack = 0
        self.sender_state = 0 
        self.last_seq = -1
        self.command = 'SYN'
        self.sequence = 0
        self.length = 0

        self.receiving = {'ACK': [], 'DAT': []}
        self.sending = []
        self.receiver_out = []

        self.unacked_list = []
        self.sender_unacked = {}
        self.receiver_acked = {}

    def create_packet(self, cmd, var1, var2, payload):
        header1, header2 = ('Acknowledge: ', 'Window: ') if cmd == 'ACK' else ('Sequence: ', 'Length: ')
        packet = f"{cmd}\n{header1}{var1}\n{header2}{var2}\n\n{payload}"
        return packet.encode()

    # Get the payload in specified length from the content
    def get_payload(self, l):
        payload = ''.join(self.file_content[:l])
        self.file_content = self.file_content[l:]

        if not self.file_content:
            self.sender_state = 2

        return payload

    # Unpack a received packet
    def process_received(self, packet):
        cmd = None
        string = packet.decode()
        arr = string.split("\n")

        # Process lines in packet
        for e in arr:
            # Find command and payload
            cmd_match = re.match(r"(SYN|DAT|FIN|ACK)", e)
            if cmd_match:
                cmd = cmd_match.group(1)
                if cmd in ["SYN", "DAT", "FIN"]:
                    if self.receiving['DAT']:
                        leftover = e.split(cmd, 1)
                        self.receiving['DAT'].append(leftover[0])
                    self.receiving['DAT'].append(cmd)
                elif cmd == "ACK":
                    self.receiving['ACK'].append(cmd)
            else:
                if cmd == "ACK":
                    self.receiving['ACK'].append(e)
                elif cmd in ["SYN", "DAT", "FIN"]:
                    self.receiving['DAT'].append(e)

    # Format log and print
    def print_log(self, state, cmd, var1, var2):
        fmt = "%a %b %d %H:%M:%S %Z %Y"
        now = datetime.datetime.now()
        local_time = now.astimezone()
        timestring = local_time.strftime(fmt)

        if cmd == 'ACK':
            header1 = 'Acknowledge: '
            header2 = 'Window: '
        else:
            header1 = 'Sequence: '
            header2 = 'Length: '

        log = f"{timestring}; {state}; {cmd}; {header1}{var1}; {header2}{var2}"
        print(log)

    # Update sequence num
    def update_seq_num(self, length):
        self.sequence += length if length >= 1 else 1

    # Timeout
    def handle_timeout(self):
        oldest_seq = min(self.unacked_list)
        oldest_packet = self.sender_unacked[oldest_seq]
        self.sock.sendto(oldest_packet, self.h2)

        tmp = oldest_packet.split()
        # Command SYN|DAT|FIN
        cmd = tmp[0].decode()
        # Sequence num
        seq = tmp[2].decode()
        # Payload length
        l = tmp[4].decode()

        self.print_log('Send', cmd, seq, l)

    def run(self):
        flag = True

        while flag:
            #self.receiving = {'ACK': [], 'DAT': []}
            readable, writable, exceptional = select.select(self.inputs, self.outputs, self.error, 0)
            
            # Process writable sockets
            for s in writable:
                # Send packets while sequence num is within the window size & sender is not FIN state
                while(self.sequence < self.receiver_ack + self.sender_window and self.sender_state != 2):
                    if(self.sender_state == 1):
                        self.command = 'DAT'
                        payload = self.get_payload(1024)
                        self.length = len(payload)
                    else:
                        payload = ""
                        self.length = 0
                        if(self.command == 'FIN'):
                            self.last_seq = self.sequence
                            self.sender_state = 2
                    packet = self.create_packet(self.command, self.sequence, self.length, payload)
                    self.sending.append(packet)
                    self.print_log('Send', self.command, self.sequence, self.length)
                    self.unacked_list.append(self.sequence)
                    self.sender_unacked[self.sequence] = packet
                    self.update_seq_num(self.length)

                # Send all packets in the sending list
                while(self.sending):
                    p = self.sending.pop(0)
                    s.sendto(p, self.h2)
                self.outputs.remove(s)
                #self.outputs.append(s)

            # Process readable sockets
            for s in readable:
                packet = s.recv(3180)
                self.process_received(packet)
                # Process received packets
                while(self.receiving['DAT']):
                    cmd = self.receiving['DAT'].pop(0)
                    header1 = self.receiving['DAT'].pop(0)
                    header1 = header1.split()
                    received_seq = int(header1[1])
                    header2 = self.receiving['DAT'].pop(0)
                    header2 = header2.split()
                    received_len = int(header2[1])

                    self.receiving['DAT'].pop(0)
                    if(cmd != 'DAT'):
                        self.receiving['DAT'].pop(0)

                    # Get payload of received packet
                    if(cmd == 'DAT' and received_len >= 1):
                        payload = ""
                        while(self.receiving['DAT'] and len(payload) < received_len):
                            payload += self.receiving['DAT'].pop(0)
                            if(len(payload) < received_len):
                                payload += '\n'
                                if(len(payload) == received_len):
                                    self.receiving['DAT'].pop(0)
                    self.print_log('Receive', cmd, received_seq, received_len)

                    # Handle received packets
                    if(received_seq < self.receiver_ack):
                        packet = self.create_packet("ACK", self.receiver_ack, self.receiver_window, "")
                        self.receiver_out.append(packet)
                        self.print_log('Send', 'ACK', self.receiver_ack, self.receiver_window)
                    
                    elif(received_seq > self.receiver_ack + self.receiver_window):
                        pass

                    elif(received_seq != self.receiver_ack):
                        self.receiver_acked[received_seq] = payload
                        packet = self.create_packet("ACK", self.receiver_ack, self.receiver_window, "")
                        self.receiver_out.append(packet)
                        self.print_log('Send', 'ACK', self.receiver_ack, self.receiver_window)
                    else:
                        self.receiver_acked[received_seq] = payload
                        while(received_seq == self.receiver_ack):
                            payload = self.receiver_acked[received_seq]
                            self.file_write.write(payload)

                            if(received_len == 0):
                                self.receiver_ack += 1
                            else:
                                self.receiver_ack += len(payload)

                            if(self.receiver_ack in self.receiver_acked):
                                received_seq = self.receiver_ack

                            self.receiver_window = self.receiver_window - len(payload)
                            if(self.receiver_window == 0):
                                self.receiver_window = 1024 * 4

                            packet = self.create_packet("ACK", self.receiver_ack, self.receiver_window, "")
                            self.receiver_out.append(packet)
                            self.print_log('Send', 'ACK', self.receiver_ack, self.receiver_window)

                    if(not self.receiving['DAT']):
                        while(self.receiver_out):
                            p = self.receiver_out.pop(0)
                            s.sendto(p, self.h2)

                # Process received ACK
                while(self.receiving['ACK']):
                    cmd = self.receiving['ACK'].pop(0)
                    if(cmd == ""):
                        break
                    header1 = self.receiving['ACK'].pop(0)
                    header1 = header1.split()
                    received_ack = int(header1[1])
                    header2 = self.receiving['ACK'].pop(0)
                    header2 = header2.split()
                    received_window = int(header2[1])
                    self.sender_window = received_window

                    self.receiving['ACK'].pop(0)

                    if(received_ack >= 1 and self.sender_state != 2):
                        self.sender_state = 1

                    self.print_log('Receive', cmd, received_ack, received_window)
                    for seq_num in self.unacked_list:
                        if(seq_num < received_ack):
                            self.unacked_list.remove(seq_num)
                            del self.sender_unacked[seq_num]

                    if(self.sender_state == 2 and len(self.sender_unacked) == 0):
                        self.command = 'FIN'
                        self.sender_state = 0
                    if(received_ack - 1 == self.last_seq):
                        self.inputs.remove(s)
                        flag = False
                        break
                    if(received_window >= 1):
                        self.outputs.append(s)

            # Timeout
            if not (readable or writable or exceptional):
                self.handle_timeout()
                continue

        # Close socket and file
        self.sock.close()
        self.file_write.close()

def main():
    # Get inputs
    ip_addr = sys.argv[1]
    port_number = int(sys.argv[2])
    read_file_name = sys.argv[3]
    write_file_name = sys.argv[4]

    server = Server(ip_addr, port_number, read_file_name, write_file_name)
    server.run()

if __name__ == "__main__":
    main()
