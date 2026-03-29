using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Windows.Forms;
using PacketLoggerGUI.Bot;

namespace PacketLoggerGUI
{
    public class MainForm : Form
    {
        // Process selector controls
        private Panel processPanel;
        private ListBox processListBox;
        private Button refreshButton;
        private Button injectButton;

        // Packet logger controls
        private Panel toolPanel;
        private Panel filterPanel;
        private ListView packetListView;
        private TextBox filterTextBox;
        private TextBox sendTextBox;
        private Button sendButton;
        private Button disconnectButton;
        private Button clearButton;
        private Button exportButton;
        private CheckBox showRecvCheckBox;
        private CheckBox showSendCheckBox;
        private CheckBox autoScrollCheckBox;
        private Label statusLabel;
        private Label packetCountLabel;
        private Panel sendPanel;

        private PipeClient? pipeClient;
        private List<PacketEntry> allPackets = new List<PacketEntry>();
        private object lockObj = new object();
        private List<NosTaleProcess> foundProcesses = new List<NosTaleProcess>();
        private FileSystemWatcher? cmdWatcher;
        private static readonly string CmdFilePath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "NosTalePacketCmd.txt");

        // Bot
        private GameState? gameState;
        private BotEngine? botEngine;
        private Button botToggleButton;
        private Label botStatusLabel;

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
            RefreshProcessList();
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

            // === PROCESS SELECTOR PANEL (shown initially) ===
            processPanel = new Panel
            {
                Dock = DockStyle.Fill,
                BackColor = Color.FromArgb(30, 30, 30),
                Padding = new Padding(40)
            };

            var titleLabel = new Label
            {
                Text = "NosTale Packet Logger",
                Font = new Font("Segoe UI", 20, FontStyle.Bold),
                ForeColor = Color.FromArgb(0, 180, 255),
                AutoSize = true,
                Location = new Point(40, 30)
            };

            var subtitleLabel = new Label
            {
                Text = "Select a NosTale process to inject and start logging packets",
                Font = new Font("Segoe UI", 10),
                ForeColor = Color.Gray,
                AutoSize = true,
                Location = new Point(42, 65)
            };

            processListBox = new ListBox
            {
                Location = new Point(40, 100),
                Size = new Size(600, 200),
                BackColor = Color.FromArgb(40, 40, 40),
                ForeColor = Color.White,
                Font = new Font("Consolas", 11),
                BorderStyle = BorderStyle.FixedSingle
            };
            processListBox.DoubleClick += (s, e) => InjectButton_Click(s, e);

            refreshButton = new Button
            {
                Text = "Refresh",
                Size = new Size(100, 35),
                Location = new Point(40, 315),
                BackColor = Color.FromArgb(60, 60, 60),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 10)
            };
            refreshButton.Click += (s, e) => RefreshProcessList();

            injectButton = new Button
            {
                Text = "Inject && Connect",
                Size = new Size(160, 35),
                Location = new Point(150, 315),
                BackColor = Color.FromArgb(0, 122, 204),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 10, FontStyle.Bold)
            };
            injectButton.Click += InjectButton_Click;

            var hintLabel = new Label
            {
                Text = "Tip: Double-click a process to inject. PIDs are shown in the game window titles.",
                Font = new Font("Segoe UI", 9),
                ForeColor = Color.FromArgb(120, 120, 120),
                AutoSize = true,
                Location = new Point(42, 365)
            };

            processPanel.Controls.AddRange(new Control[] {
                titleLabel, subtitleLabel, processListBox, refreshButton, injectButton, hintLabel
            });

            // === PACKET LOGGER CONTROLS (shown after injection) ===

            // Top toolbar
            toolPanel = new Panel
            {
                Dock = DockStyle.Top,
                Height = 45,
                BackColor = Color.FromArgb(45, 45, 45),
                Padding = new Padding(8, 8, 8, 8),
                Visible = false
            };

            disconnectButton = new Button
            {
                Text = "Disconnect",
                Size = new Size(100, 28),
                Location = new Point(8, 8),
                BackColor = Color.FromArgb(180, 50, 50),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            disconnectButton.Click += DisconnectButton_Click;

            clearButton = new Button
            {
                Text = "Clear",
                Size = new Size(70, 28),
                Location = new Point(115, 8),
                BackColor = Color.FromArgb(60, 60, 60),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            clearButton.Click += (s, e) => { lock (lockObj) { allPackets.Clear(); } ApplyFilter(); };

            exportButton = new Button
            {
                Text = "Export",
                Size = new Size(70, 28),
                Location = new Point(192, 8),
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
                Location = new Point(280, 12),
                ForeColor = Color.Cyan
            };
            showRecvCheckBox.CheckedChanged += (s, e) => ApplyFilter();

            showSendCheckBox = new CheckBox
            {
                Text = "SEND",
                Checked = true,
                AutoSize = true,
                Location = new Point(350, 12),
                ForeColor = Color.Yellow
            };
            showSendCheckBox.CheckedChanged += (s, e) => ApplyFilter();

            autoScrollCheckBox = new CheckBox
            {
                Text = "Auto-scroll",
                Checked = true,
                AutoSize = true,
                Location = new Point(420, 12),
                ForeColor = Color.LightGray
            };

            packetCountLabel = new Label
            {
                AutoSize = true,
                Location = new Point(530, 14),
                ForeColor = Color.Gray,
                Text = "Packets: 0"
            };

            botToggleButton = new Button
            {
                Text = "Start Bot",
                Size = new Size(90, 28),
                Location = new Point(630, 8),
                BackColor = Color.FromArgb(0, 160, 0),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            botToggleButton.Click += BotToggleButton_Click;

            botStatusLabel = new Label
            {
                AutoSize = true,
                Location = new Point(730, 14),
                ForeColor = Color.FromArgb(150, 150, 150),
                Text = "Bot: OFF"
            };

            toolPanel.Controls.AddRange(new Control[] {
                disconnectButton, clearButton, exportButton,
                showRecvCheckBox, showSendCheckBox, autoScrollCheckBox, packetCountLabel,
                botToggleButton, botStatusLabel
            });

            // Filter panel
            filterPanel = new Panel
            {
                Dock = DockStyle.Top,
                Height = 35,
                BackColor = Color.FromArgb(40, 40, 40),
                Padding = new Padding(8, 4, 8, 4),
                Visible = false
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
                VirtualMode = false,
                Visible = false
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

            packetListView.KeyDown += (s, e) =>
            {
                if (e.Control && e.KeyCode == Keys.C)
                {
                    CopySelectedPacket();
                    e.Handled = true;
                }
            };

            packetListView.DoubleClick += (s, e) => SendSelectedPacket();

            // Send panel
            sendPanel = new Panel
            {
                Dock = DockStyle.Bottom,
                Height = 45,
                BackColor = Color.FromArgb(45, 45, 45),
                Padding = new Padding(8, 8, 8, 8),
                Visible = false
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
            sendTextBox.PlaceholderText = "Type packet(s) to send — use ; to chain multiple";
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
                BackColor = Color.FromArgb(60, 60, 60),
                ForeColor = Color.White,
                Text = "  Select a process and click Inject && Connect",
                TextAlign = ContentAlignment.MiddleLeft
            };

            // Add controls — order matters for docking
            this.Controls.Add(packetListView);
            this.Controls.Add(processPanel);
            this.Controls.Add(filterPanel);
            this.Controls.Add(toolPanel);
            this.Controls.Add(sendPanel);
            this.Controls.Add(statusLabel);

            // Handle resize
            this.Resize += (s, e) =>
            {
                // Process panel
                processListBox.Width = this.ClientSize.Width - 80;
                injectButton.Left = refreshButton.Right + 10;

                // Packet logger
                sendTextBox.Width = this.ClientSize.Width - 160;
                sendButton.Left = sendTextBox.Right + 10;
                filterTextBox.Width = this.ClientSize.Width - 80;
                if (packetListView.Columns.Count > 3)
                    packetListView.Columns[3].Width = this.ClientSize.Width - 260;
            };
        }

        // ===== PROCESS SELECTOR =====

        private void RefreshProcessList()
        {
            processListBox.Items.Clear();
            foundProcesses = ProcessInjector.FindNosTaleProcesses();

            // Tag windows with PID
            foreach (var proc in foundProcesses)
                ProcessInjector.TagWindowWithPID(proc.Pid);

            // Re-read titles after tagging
            foundProcesses = ProcessInjector.FindNosTaleProcesses();

            if (foundProcesses.Count == 0)
            {
                processListBox.Items.Add("No NosTale processes found. Make sure the game is running.");
                injectButton.Enabled = false;
            }
            else
            {
                foreach (var proc in foundProcesses)
                    processListBox.Items.Add(proc.ToString());

                processListBox.SelectedIndex = 0;
                injectButton.Enabled = true;
            }

            SetStatus($"Found {foundProcesses.Count} NosTale process(es)", Color.FromArgb(60, 60, 60));
        }

        private async void InjectButton_Click(object? sender, EventArgs e)
        {
            if (processListBox.SelectedIndex < 0 || processListBox.SelectedIndex >= foundProcesses.Count)
            {
                SetStatus("Select a process first", Color.FromArgb(200, 150, 0));
                return;
            }

            var selected = foundProcesses[processListBox.SelectedIndex];

            injectButton.Enabled = false;
            refreshButton.Enabled = false;
            SetStatus($"Injecting into PID {selected.Pid}...", Color.FromArgb(200, 150, 0));

            // Find Injector.exe — look next to this exe, then in Release/
            string exeDir = AppDomain.CurrentDomain.BaseDirectory;
            string injectorPath = Path.Combine(exeDir, "Injector.exe");

            if (!File.Exists(injectorPath))
            {
                string? searchDir = Path.GetDirectoryName(exeDir.TrimEnd(Path.DirectorySeparatorChar));
                while (searchDir != null)
                {
                    string candidate = Path.Combine(searchDir, "Release", "Injector.exe");
                    if (File.Exists(candidate))
                    {
                        injectorPath = candidate;
                        break;
                    }
                    searchDir = Path.GetDirectoryName(searchDir);
                }
            }

            if (!File.Exists(injectorPath))
            {
                SetStatus("Injector.exe not found! Place it next to the GUI exe or in Release/", Color.FromArgb(200, 50, 50));
                injectButton.Enabled = true;
                refreshButton.Enabled = true;
                return;
            }

            // Launch the C++ Injector with --pid for reliable injection
            try
            {
                var startInfo = new System.Diagnostics.ProcessStartInfo
                {
                    FileName = injectorPath,
                    Arguments = $"--pid {selected.Pid}",
                    UseShellExecute = true,
                    Verb = "runas", // ensure admin
                    WorkingDirectory = Path.GetDirectoryName(injectorPath) ?? exeDir
                };

                var proc = System.Diagnostics.Process.Start(startInfo);
                proc?.WaitForExit(5000);

                if (proc == null || proc.ExitCode != 0)
                {
                    SetStatus($"Injection failed for PID {selected.Pid} (Injector exit code: {proc?.ExitCode})", Color.FromArgb(200, 50, 50));
                    injectButton.Enabled = true;
                    refreshButton.Enabled = true;
                    return;
                }
            }
            catch (Exception ex)
            {
                SetStatus($"Failed to launch Injector: {ex.Message}", Color.FromArgb(200, 50, 50));
                injectButton.Enabled = true;
                refreshButton.Enabled = true;
                return;
            }

            SetStatus($"Injected into PID {selected.Pid}. Waiting for DLL to initialize...", Color.FromArgb(200, 150, 0));

            // Try connecting with retries — DLL needs time to start up and create pipes
            const int maxRetries = 3;
            for (int attempt = 1; attempt <= maxRetries; attempt++)
            {
                await System.Threading.Tasks.Task.Delay(1000);
                SetStatus($"Connecting to pipe (attempt {attempt}/{maxRetries})...", Color.FromArgb(200, 150, 0));

                try
                {
                    pipeClient = new PipeClient();
                    pipeClient.PacketReceived += OnPacketReceived;
                    pipeClient.StatusReceived += OnStatusReceived;
                    pipeClient.Disconnected += OnDisconnected;

                    await pipeClient.ConnectAsync(5000);

                    // Success — switch to packet logger view
                    ShowPacketLoggerView(selected);
                    return;
                }
                catch (Exception)
                {
                    pipeClient?.Dispose();
                    pipeClient = null;
                }
            }

            SetStatus($"Pipe connection failed after {maxRetries} attempts. Is the DLL console visible?", Color.FromArgb(200, 50, 50));
            injectButton.Enabled = true;
            refreshButton.Enabled = true;
        }

        private void ShowPacketLoggerView(NosTaleProcess proc)
        {
            this.Text = $"NosTale Packet Logger — PID {proc.Pid} — {proc.WindowTitle}";

            // Init bot systems
            gameState = new GameState();
            gameState.OnLog += msg =>
            {
                if (this.IsHandleCreated)
                    this.BeginInvoke(() => SetStatus(msg, Color.FromArgb(0, 122, 204)));
            };

            // Hide process selector, show logger
            processPanel.Visible = false;
            toolPanel.Visible = true;
            filterPanel.Visible = true;
            packetListView.Visible = true;
            sendPanel.Visible = true;

            StartCommandFileWatcher();
            SetStatus($"Connected to PID {proc.Pid} — Logging packets | Cmd file: {CmdFilePath}", Color.FromArgb(0, 150, 0));
        }

        private void ShowProcessSelectorView()
        {
            this.Text = "NosTale Packet Logger";

            // Show process selector, hide logger
            processPanel.Visible = true;
            toolPanel.Visible = false;
            filterPanel.Visible = false;
            packetListView.Visible = false;
            sendPanel.Visible = false;

            injectButton.Enabled = true;
            refreshButton.Enabled = true;

            RefreshProcessList();
        }

        private void DisconnectButton_Click(object? sender, EventArgs e)
        {
            botEngine?.Stop();
            botEngine = null;
            gameState = null;
            StopCommandFileWatcher();
            pipeClient?.Dispose();
            pipeClient = null;
            botToggleButton.Text = "Start Bot";
            botToggleButton.BackColor = Color.FromArgb(0, 160, 0);
            botStatusLabel.Text = "Bot: OFF";
            ShowProcessSelectorView();
            SetStatus("Disconnected", Color.FromArgb(200, 50, 50));
        }

        // ===== PACKET HANDLING =====

        private void OnPacketReceived(object? sender, PacketReceivedEventArgs e)
        {
            // Feed packet to game state model
            gameState?.ProcessPacket(e.Direction, e.Packet.TrimEnd('\n', '\r'));

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
                    ShowProcessSelectorView();
                    SetStatus("Disconnected — DLL pipe closed", Color.FromArgb(200, 50, 50));
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
                SetStatus("Not connected — cannot send", Color.FromArgb(200, 50, 50));
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

        // ===== BOT CONTROLS =====

        private void BotToggleButton_Click(object? sender, EventArgs e)
        {
            if (botEngine != null && botEngine.IsRunning)
            {
                botEngine.Stop();
                botToggleButton.Text = "Start Bot";
                botToggleButton.BackColor = Color.FromArgb(0, 160, 0);
                botStatusLabel.Text = "Bot: OFF";
                botStatusLabel.ForeColor = Color.FromArgb(150, 150, 150);
                return;
            }

            if (pipeClient == null || !pipeClient.IsConnected || gameState == null)
            {
                SetStatus("Not connected — cannot start bot", Color.FromArgb(200, 50, 50));
                return;
            }

            var packetSender = new PacketSender(pipeClient);
            botEngine = new BotEngine(gameState, packetSender);
            botEngine.OnLog += msg =>
            {
                if (this.IsHandleCreated)
                    this.BeginInvoke(() => SetStatus(msg, Color.FromArgb(0, 122, 204)));
            };

            // Update bot status label periodically
            var timer = new System.Windows.Forms.Timer { Interval = 500 };
            timer.Tick += (s2, e2) =>
            {
                if (botEngine == null || !botEngine.IsRunning)
                {
                    timer.Stop();
                    timer.Dispose();
                    return;
                }
                botStatusLabel.Text = $"Bot: {botEngine.State} | HP:{gameState.Self.HpPercent:F0}% | Pos:({gameState.Self.X},{gameState.Self.Y}) | Mobs:{gameState.Entities.Values.Count(x => x.IsMonster)}";
                botStatusLabel.ForeColor = Color.FromArgb(0, 255, 100);
            };

            botEngine.Start();
            timer.Start();
            botToggleButton.Text = "Stop Bot";
            botToggleButton.BackColor = Color.FromArgb(200, 50, 50);
            botStatusLabel.Text = "Bot: Starting...";
            botStatusLabel.ForeColor = Color.FromArgb(0, 255, 100);
        }

        // ===== COMMAND FILE WATCHER =====
        // Claude (or any tool) can write packets to C:\NosTalePacketCmd.txt
        // One packet per line. The GUI reads them, sends them, and clears the file.

        private void StartCommandFileWatcher()
        {
            try
            {
                // Clear any stale commands
                if (File.Exists(CmdFilePath))
                    File.WriteAllText(CmdFilePath, "");
                else
                    File.Create(CmdFilePath).Dispose();

                cmdWatcher = new FileSystemWatcher
                {
                    Path = Path.GetDirectoryName(CmdFilePath)!,
                    Filter = Path.GetFileName(CmdFilePath),
                    NotifyFilter = NotifyFilters.LastWrite | NotifyFilters.Size
                };
                cmdWatcher.Changed += OnCommandFileChanged;
                cmdWatcher.EnableRaisingEvents = true;
            }
            catch { }
        }

        private void StopCommandFileWatcher()
        {
            if (cmdWatcher != null)
            {
                cmdWatcher.EnableRaisingEvents = false;
                cmdWatcher.Dispose();
                cmdWatcher = null;
            }
        }

        private void OnCommandFileChanged(object sender, FileSystemEventArgs e)
        {
            try
            {
                // Small delay to let the writer finish
                System.Threading.Thread.Sleep(100);

                string[] lines = File.ReadAllLines(CmdFilePath);
                if (lines.Length == 0) return;

                // Clear the file immediately
                File.WriteAllText(CmdFilePath, "");

                if (pipeClient == null || !pipeClient.IsConnected) return;

                foreach (string line in lines)
                {
                    string trimmed = line.Trim();
                    if (string.IsNullOrEmpty(trimmed) || trimmed.StartsWith("#"))
                        continue; // skip empty lines and comments

                    pipeClient.SendPacket(trimmed);

                    if (this.IsHandleCreated)
                    {
                        this.BeginInvoke(() =>
                            SetStatus($"Cmd file sent: {trimmed}", Color.FromArgb(0, 150, 0)));
                    }
                }
            }
            catch { }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            StopCommandFileWatcher();
            pipeClient?.Dispose();
            base.OnFormClosing(e);
        }
    }
}
