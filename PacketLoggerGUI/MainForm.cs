using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Windows.Forms;

namespace PacketLoggerGUI
{
    public class MainForm : Form
    {
        private ListView packetListView;
        private TextBox filterTextBox;
        private TextBox sendTextBox;
        private Button sendButton;
        private Button connectButton;
        private Button clearButton;
        private Button exportButton;
        private CheckBox showRecvCheckBox;
        private CheckBox showSendCheckBox;
        private CheckBox autoScrollCheckBox;
        private Label statusLabel;
        private Label packetCountLabel;

        private PipeClient? pipeClient;
        private List<PacketEntry> allPackets = new List<PacketEntry>();
        private object lockObj = new object();

        private class PacketEntry
        {
            public string Time { get; set; } = "";
            public string Direction { get; set; } = "";
            public string Opcode { get; set; } = "";
            public string Packet { get; set; } = "";
        }

        public MainForm()
        {
            InitializeComponent();
        }

        private void InitializeComponent()
        {
            this.Text = "NosTale Packet Logger";
            this.Size = new Size(1000, 700);
            this.MinimumSize = new Size(800, 500);
            this.StartPosition = FormStartPosition.CenterScreen;
            this.Font = new Font("Segoe UI", 9);
            this.BackColor = Color.FromArgb(30, 30, 30);
            this.ForeColor = Color.White;

            // Top toolbar panel
            var toolPanel = new Panel
            {
                Dock = DockStyle.Top,
                Height = 45,
                BackColor = Color.FromArgb(45, 45, 45),
                Padding = new Padding(8, 8, 8, 8)
            };

            connectButton = new Button
            {
                Text = "Connect",
                Size = new Size(90, 28),
                Location = new Point(8, 8),
                BackColor = Color.FromArgb(0, 122, 204),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            connectButton.Click += ConnectButton_Click;

            clearButton = new Button
            {
                Text = "Clear",
                Size = new Size(70, 28),
                Location = new Point(105, 8),
                BackColor = Color.FromArgb(60, 60, 60),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            clearButton.Click += (s, e) => { lock (lockObj) { allPackets.Clear(); } ApplyFilter(); };

            exportButton = new Button
            {
                Text = "Export",
                Size = new Size(70, 28),
                Location = new Point(182, 8),
                BackColor = Color.FromArgb(60, 60, 60),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            exportButton.Click += ExportButton_Click;

            showRecvCheckBox = new CheckBox
            {
                Text = "RECV",
                Checked = true,
                AutoSize = true,
                Location = new Point(270, 12),
                ForeColor = Color.Cyan
            };
            showRecvCheckBox.CheckedChanged += (s, e) => ApplyFilter();

            showSendCheckBox = new CheckBox
            {
                Text = "SEND",
                Checked = true,
                AutoSize = true,
                Location = new Point(340, 12),
                ForeColor = Color.Yellow
            };
            showSendCheckBox.CheckedChanged += (s, e) => ApplyFilter();

            autoScrollCheckBox = new CheckBox
            {
                Text = "Auto-scroll",
                Checked = true,
                AutoSize = true,
                Location = new Point(410, 12),
                ForeColor = Color.LightGray
            };

            packetCountLabel = new Label
            {
                AutoSize = true,
                Location = new Point(520, 14),
                ForeColor = Color.Gray,
                Text = "Packets: 0"
            };

            toolPanel.Controls.AddRange(new Control[] {
                connectButton, clearButton, exportButton,
                showRecvCheckBox, showSendCheckBox, autoScrollCheckBox, packetCountLabel
            });

            // Filter panel
            var filterPanel = new Panel
            {
                Dock = DockStyle.Top,
                Height = 35,
                BackColor = Color.FromArgb(40, 40, 40),
                Padding = new Padding(8, 4, 8, 4)
            };

            var filterLabel = new Label
            {
                Text = "Filter:",
                AutoSize = true,
                Location = new Point(8, 8),
                ForeColor = Color.LightGray
            };

            filterTextBox = new TextBox
            {
                Location = new Point(55, 5),
                Size = new Size(400, 24),
                BackColor = Color.FromArgb(50, 50, 50),
                ForeColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle
            };
            filterTextBox.TextChanged += (s, e) => ApplyFilter();
            filterTextBox.PlaceholderText = "Type to filter packets (e.g. walk, mv, say, su ...)";

            filterPanel.Controls.AddRange(new Control[] { filterLabel, filterTextBox });

            // Packet list
            packetListView = new ListView
            {
                Dock = DockStyle.Fill,
                View = View.Details,
                FullRowSelect = true,
                BackColor = Color.FromArgb(25, 25, 25),
                ForeColor = Color.White,
                Font = new Font("Consolas", 9),
                GridLines = true,
                VirtualMode = false
            };

            packetListView.Columns.Add("Time", 80);
            packetListView.Columns.Add("Dir", 55);
            packetListView.Columns.Add("Opcode", 90);
            packetListView.Columns.Add("Packet", 700);

            // Right-click context menu
            var contextMenu = new ContextMenuStrip();
            contextMenu.BackColor = Color.FromArgb(45, 45, 45);
            contextMenu.ForeColor = Color.White;

            var copyItem = new ToolStripMenuItem("Copy Packet\tCtrl+C");
            copyItem.Click += (s, e) => CopySelectedPacket();

            var sendItem = new ToolStripMenuItem("Send This Packet");
            sendItem.Click += (s, e) => SendSelectedPacket();

            var copyToSendBox = new ToolStripMenuItem("Copy to Send Box");
            copyToSendBox.Click += (s, e) => CopyToSendBox();

            contextMenu.Items.Add(copyItem);
            contextMenu.Items.Add(sendItem);
            contextMenu.Items.Add(new ToolStripSeparator());
            contextMenu.Items.Add(copyToSendBox);

            packetListView.ContextMenuStrip = contextMenu;

            // Ctrl+C keyboard shortcut
            packetListView.KeyDown += (s, e) =>
            {
                if (e.Control && e.KeyCode == Keys.C)
                {
                    CopySelectedPacket();
                    e.Handled = true;
                }
            };

            // Double-click to send packet
            packetListView.DoubleClick += (s, e) => SendSelectedPacket();

            // Send panel at bottom
            var sendPanel = new Panel
            {
                Dock = DockStyle.Bottom,
                Height = 45,
                BackColor = Color.FromArgb(45, 45, 45),
                Padding = new Padding(8, 8, 8, 8)
            };

            var sendLabel = new Label
            {
                Text = "Send:",
                AutoSize = true,
                Location = new Point(8, 14),
                ForeColor = Color.LightGray
            };

            sendTextBox = new TextBox
            {
                Location = new Point(55, 10),
                Size = new Size(600, 24),
                BackColor = Color.FromArgb(50, 50, 50),
                ForeColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle
            };
            sendTextBox.PlaceholderText = "Type packet(s) to send — use ; to chain multiple (e.g. walk 60 95 1 10;ptctl 20 1 ID 60 95 ID 10)";
            sendTextBox.KeyDown += (s, e) =>
            {
                if (e.KeyCode == Keys.Enter)
                {
                    SendPacket();
                    e.SuppressKeyPress = true;
                }
            };

            sendButton = new Button
            {
                Text = "Send",
                Size = new Size(80, 28),
                Location = new Point(665, 8),
                BackColor = Color.FromArgb(0, 122, 204),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            sendButton.Click += (s, e) => SendPacket();

            sendPanel.Controls.AddRange(new Control[] { sendLabel, sendTextBox, sendButton });

            // Status bar
            statusLabel = new Label
            {
                Dock = DockStyle.Bottom,
                Height = 25,
                BackColor = Color.FromArgb(0, 122, 204),
                ForeColor = Color.White,
                Text = "  Disconnected - Click Connect after injecting the DLL",
                TextAlign = ContentAlignment.MiddleLeft
            };

            // Layout order matters (last added = on top for Dock)
            this.Controls.Add(packetListView);
            this.Controls.Add(filterPanel);
            this.Controls.Add(toolPanel);
            this.Controls.Add(sendPanel);
            this.Controls.Add(statusLabel);

            // Handle resize for send panel
            this.Resize += (s, e) =>
            {
                sendTextBox.Width = this.ClientSize.Width - 160;
                sendButton.Left = sendTextBox.Right + 10;
                filterTextBox.Width = this.ClientSize.Width - 80;
                packetListView.Columns[3].Width = this.ClientSize.Width - 260;
            };
        }

        private async void ConnectButton_Click(object? sender, EventArgs e)
        {
            if (pipeClient != null && pipeClient.IsConnected)
            {
                pipeClient.SendQuit();
                pipeClient.Dispose();
                pipeClient = null;
                connectButton.Text = "Connect";
                SetStatus("Disconnected", Color.FromArgb(200, 50, 50));
                return;
            }

            connectButton.Text = "Connecting...";
            connectButton.Enabled = false;
            SetStatus("Waiting for DLL pipe...", Color.FromArgb(200, 150, 0));

            try
            {
                pipeClient = new PipeClient();
                pipeClient.PacketReceived += OnPacketReceived;
                pipeClient.StatusReceived += OnStatusReceived;
                pipeClient.Disconnected += OnDisconnected;

                await pipeClient.ConnectAsync();

                connectButton.Text = "Disconnect";
                connectButton.Enabled = true;
                SetStatus("Connected - Logging packets", Color.FromArgb(0, 150, 0));
            }
            catch (Exception ex)
            {
                connectButton.Text = "Connect";
                connectButton.Enabled = true;
                SetStatus("Connection failed: " + ex.Message, Color.FromArgb(200, 50, 50));
                pipeClient?.Dispose();
                pipeClient = null;
            }
        }

        private void OnPacketReceived(object? sender, PacketReceivedEventArgs e)
        {
            var entry = new PacketEntry
            {
                Time = e.Timestamp.ToString("HH:mm:ss.fff"),
                Direction = e.Direction,
                Opcode = ExtractOpcode(e.Packet),
                Packet = e.Packet.TrimEnd('\n', '\r')
            };

            lock (lockObj)
            {
                allPackets.Add(entry);
            }

            if (this.IsHandleCreated)
            {
                this.BeginInvoke(() =>
                {
                    packetCountLabel.Text = $"Packets: {allPackets.Count}";
                    if (MatchesFilter(entry))
                        AddPacketToList(entry);
                });
            }
        }

        private void OnStatusReceived(object? sender, string status)
        {
            if (this.IsHandleCreated)
                this.BeginInvoke(() => SetStatus(status, Color.FromArgb(0, 122, 204)));
        }

        private void OnDisconnected(object? sender, EventArgs e)
        {
            if (this.IsHandleCreated)
            {
                this.BeginInvoke(() =>
                {
                    connectButton.Text = "Connect";
                    connectButton.Enabled = true;
                    SetStatus("Disconnected", Color.FromArgb(200, 50, 50));
                });
            }
        }

        private void AddPacketToList(PacketEntry entry)
        {
            var item = new ListViewItem(entry.Time);
            item.SubItems.Add(entry.Direction);
            item.SubItems.Add(entry.Opcode);
            item.SubItems.Add(entry.Packet);

            if (entry.Direction == "RECV")
                item.ForeColor = Color.Cyan;
            else
                item.ForeColor = Color.Yellow;

            packetListView.Items.Add(item);

            if (autoScrollCheckBox.Checked)
                packetListView.EnsureVisible(packetListView.Items.Count - 1);
        }

        private void ApplyFilter()
        {
            packetListView.BeginUpdate();
            packetListView.Items.Clear();

            List<PacketEntry> snapshot;
            lock (lockObj)
            {
                snapshot = new List<PacketEntry>(allPackets);
            }

            foreach (var entry in snapshot)
            {
                if (MatchesFilter(entry))
                    AddPacketToList(entry);
            }
            packetListView.EndUpdate();
        }

        private bool MatchesFilter(PacketEntry entry)
        {
            if (entry.Direction == "RECV" && !showRecvCheckBox.Checked)
                return false;
            if (entry.Direction == "SEND" && !showSendCheckBox.Checked)
                return false;

            string filter = filterTextBox.Text.Trim();
            if (string.IsNullOrEmpty(filter))
                return true;

            return entry.Packet.Contains(filter, StringComparison.OrdinalIgnoreCase)
                || entry.Opcode.Contains(filter, StringComparison.OrdinalIgnoreCase);
        }

        private string ExtractOpcode(string packet)
        {
            string trimmed = packet.TrimStart();
            int spaceIndex = trimmed.IndexOf(' ');
            if (spaceIndex > 0)
                return trimmed.Substring(0, spaceIndex);
            return trimmed.Length > 20 ? trimmed.Substring(0, 20) : trimmed;
        }

        private void SendPacket()
        {
            string text = sendTextBox.Text.Trim();
            if (string.IsNullOrEmpty(text) || pipeClient == null || !pipeClient.IsConnected)
                return;

            // Support multi-packet: separate with ;
            // e.g. "walk 60 95 1 10;ptctl 20 1 1249305 60 95 1249305 10"
            string[] packets = text.Split(';', StringSplitOptions.RemoveEmptyEntries);
            foreach (string packet in packets)
            {
                string trimmed = packet.Trim();
                if (!string.IsNullOrEmpty(trimmed))
                    pipeClient.SendPacket(trimmed);
            }

            sendTextBox.Clear();
            sendTextBox.Focus();
        }

        private void ExportButton_Click(object? sender, EventArgs e)
        {
            using var dialog = new SaveFileDialog
            {
                Filter = "Log files (*.log)|*.log|Text files (*.txt)|*.txt|All files (*.*)|*.*",
                DefaultExt = "log",
                FileName = $"packets_{DateTime.Now:yyyyMMdd_HHmmss}.log"
            };

            if (dialog.ShowDialog() == DialogResult.OK)
            {
                List<PacketEntry> snapshot;
                lock (lockObj)
                {
                    snapshot = new List<PacketEntry>(allPackets);
                }

                using var writer = new StreamWriter(dialog.FileName);
                foreach (var entry in snapshot)
                {
                    writer.WriteLine($"{entry.Time} [{entry.Direction}] {entry.Packet}");
                }

                SetStatus($"Exported {snapshot.Count} packets to {dialog.FileName}", Color.FromArgb(0, 122, 204));
            }
        }

        private void SetStatus(string text, Color color)
        {
            statusLabel.Text = "  " + text;
            statusLabel.BackColor = color;
        }

        private void CopySelectedPacket()
        {
            if (packetListView.SelectedItems.Count == 0)
                return;

            var item = packetListView.SelectedItems[0];
            string packet = item.SubItems[3].Text;
            Clipboard.SetText(packet);
            SetStatus("Copied: " + packet, Color.FromArgb(0, 122, 204));
        }

        private void SendSelectedPacket()
        {
            if (packetListView.SelectedItems.Count == 0)
                return;
            if (pipeClient == null || !pipeClient.IsConnected)
            {
                SetStatus("Not connected - cannot send", Color.FromArgb(200, 50, 50));
                return;
            }

            var item = packetListView.SelectedItems[0];
            string packet = item.SubItems[3].Text;
            pipeClient.SendPacket(packet);
            SetStatus("Sent: " + packet, Color.FromArgb(0, 150, 0));
        }

        private void CopyToSendBox()
        {
            if (packetListView.SelectedItems.Count == 0)
                return;

            var item = packetListView.SelectedItems[0];
            sendTextBox.Text = item.SubItems[3].Text;
            sendTextBox.Focus();
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            pipeClient?.Dispose();
            base.OnFormClosing(e);
        }
    }
}
