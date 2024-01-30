# Reference: sws_psudo.py provided by the course tutorial instructor

import select
import socket
import sys
import queue
import datetime
import re
import os

# Create a TCP/IP socket
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setblocking(0)
serverPort = int(sys.argv[2])
# Bind the socket to the port
server_address = ('', serverPort)
server.bind(server_address)

# Listen for incoming connections
server.listen(5)
# Sockets from which we expect to read
inputs = [server]
# Sockets to which we expect to write
outputs = []
# Outgoing message queues (socket:Queue)
message_queues = {}
# request message
request_message = {}
# Connection status
connection_status = {}
# Set timeout to 30 sec
timeout = 30

def process_request(request, connection):
    # Split the request into lines
    lines = request.split("\n")
    # Check if request is in a valid format
    if len(lines) < 2 or not lines[0].startswith("GET "):
        return "HTTP/1.0 400 Bad Request\r\n\r\n", False
    
    # Get the filename from the request
    filename = lines[0].split(" ")[1][1:]
    # Check if file exists
    if not os.path.isfile(filename):
        return "HTTP/1.0 404 Not Found\r\n\r\n", False
    
    # Get the connection status
    connection_status[connection] = "close"
    for line in lines[1:]:
        if line.startswith("Connection: keep-alive"):
            connection_status[connection] = "keep-alive"
            break
    
    # Open the file and read its content
    with open(filename, "r") as f:
        file_content = f.read()
    
    # Construct the response
    response = "HTTP/1.0 200 OK\r\n\r\n" + file_content
    
    return response, True

while inputs:
    # Wait for at least one of the sockets to be
    # ready for processing
    #print('waiting for the next event', file=sys.stderr)
    readable, writable, exceptional = select.select(inputs,
                                                    outputs,
                                                    inputs,
                                                    timeout)

    # Handle inputs
    for s in readable:
        if s is server:
            connection, client_address = s.accept()
            #connection.setblocking(0)
            inputs.append(connection)
            request_message[connection] = ""
        else:
            try:
                data = s.recv(1024).decode()
                if data:
                    # A client sent data, process it
                    request_message[s] += data
                    if "\r\n\r\n" in request_message[s]:
                        # The whole request has been received
                        response, keep_alive = process_request(request_message[s], s)
                        s.sendall(response.encode())
                        if connection_status[s] == "keep-alive":
                            outputs.append(s)
                        else:
                            s.close()
                            inputs.remove(s)
                            del request_message[s]                        

                else:
                    # Interpret empty result as closed connection
                    s.close()
                    inputs.remove(s)
                    del request_message[s]
            except socket.error:
                # Error handling
                s.close()
                inputs.remove(s)
                del request_message[s]
