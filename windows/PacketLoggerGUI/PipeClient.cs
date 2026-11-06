using System;
using System.IO.Pipes;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace PacketLoggerGUI
{
    public class PacketReceivedEventArgs : EventArgs
    {
        public string Direction { get; }
        public string Packet { get; }
        public DateTime Timestamp { get; }

        public PacketReceivedEventArgs(string direction, string packet)
        {
            Direction = direction;
            Packet = packet;
            Timestamp = DateTime.Now;
        }
    }

    public class PipeClient : IDisposable
    {
        private NamedPipeClientStream? _pipePackets;
        private NamedPipeClientStream? _pipeCommands;
        private CancellationTokenSource? _cts;
        private Task? _readTask;
        private bool _connected;

        public event EventHandler<PacketReceivedEventArgs>? PacketReceived;
        public event EventHandler<string>? StatusReceived;
        public event EventHandler? Disconnected;

        public bool IsConnected => _connected;

        public async Task ConnectAsync(int timeoutMs = 10000)
        {
            _cts = new CancellationTokenSource(timeoutMs);

            _pipePackets = new NamedPipeClientStream(".", "NosTalePacketLogger_packets", PipeDirection.In);
            _pipeCommands = new NamedPipeClientStream(".", "NosTalePacketLogger_commands", PipeDirection.Out);

            await _pipePackets.ConnectAsync(_cts.Token);
            await _pipeCommands.ConnectAsync(_cts.Token);

            // Replace the timeout CTS with a fresh one for the read loop
            _cts = new CancellationTokenSource();

            _connected = true;
            _readTask = Task.Run(() => ReadLoop(_cts.Token));
        }

        private void ReadLoop(CancellationToken token)
        {
            byte[] buffer = new byte[65536];

            try
            {
                while (!token.IsCancellationRequested && _pipePackets != null && _pipePackets.IsConnected)
                {
                    int bytesRead = _pipePackets.Read(buffer, 0, buffer.Length);
                    if (bytesRead == 0)
                        break;

                    string message = Encoding.ASCII.GetString(buffer, 0, bytesRead);

                    // Protocol: "RECV|packet" or "STATUS|message"
                    int separatorIndex = message.IndexOf('|');
                    if (separatorIndex > 0)
                    {
                        string type = message.Substring(0, separatorIndex);
                        string data = message.Substring(separatorIndex + 1);

                        if (type == "RECV")
                            PacketReceived?.Invoke(this, new PacketReceivedEventArgs("RECV", data));
                        else if (type == "SEND")
                            PacketReceived?.Invoke(this, new PacketReceivedEventArgs("SEND", data));
                        else if (type == "STATUS")
                            StatusReceived?.Invoke(this, data);
                    }
                }
            }
            catch (Exception) { }

            _connected = false;
            Disconnected?.Invoke(this, EventArgs.Empty);
        }

        public void SendPacket(string packet)
        {
            if (!_connected || _pipeCommands == null)
                return;

            try
            {
                byte[] data = Encoding.ASCII.GetBytes("SEND|" + packet);
                _pipeCommands.Write(data, 0, data.Length);
                _pipeCommands.Flush();
            }
            catch (Exception) { }
        }

        public void SendQuit()
        {
            if (!_connected || _pipeCommands == null)
                return;

            try
            {
                byte[] data = Encoding.ASCII.GetBytes("QUIT|");
                _pipeCommands.Write(data, 0, data.Length);
                _pipeCommands.Flush();
            }
            catch (Exception) { }
        }

        public void Dispose()
        {
            _cts?.Cancel();
            SendQuit();
            _pipePackets?.Dispose();
            _pipeCommands?.Dispose();
            _cts?.Dispose();
        }
    }
}
