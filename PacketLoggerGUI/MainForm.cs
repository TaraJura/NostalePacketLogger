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
        // Process selector
        private Panel processPanel;
        private ListBox processListBox;
        private Button refreshButton;
        private Button injectButton;

        // Main layout after connection
        private Panel toolPanel;
        private TabControl mainTabs;

        // Tab 1: Packets
        private Panel filterPanel;
        private ListView packetListView;
        private TextBox filterTextBox;
        private TextBox sendTextBox;
        private Button sendButton;
        private CheckBox showRecvCheckBox;
        private CheckBox showSendCheckBox;
        private CheckBox autoScrollCheckBox;
        private Label packetCountLabel;

        // Tab 2: Bot
        private Button botToggleButton;
        private Label botInfoLabel;
        private ListView entityListView;
        private System.Windows.Forms.Timer botUpdateTimer;

        // Tab 3: Map
        private MapRenderer mapRenderer;

        // Toolbar
        private Button disconnectButton;
        private Button clearButton;
        private Button exportButton;
        private Label statusLabel;
        private Panel sendPanel;

        // State
        private PipeClient? pipeClient;
        private List<PacketEntry> allPackets = new();
        private object lockObj = new();
        private List<NosTaleProcess> foundProcesses = new();
        private FileSystemWatcher? cmdWatcher;
        private static readonly string CmdFilePath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "NosTalePacketCmd.txt");

        // Bot
        private GameState? gameState;
        private BotEngine? botEngine;

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
            this.Size = new Size(1100, 750);
            this.MinimumSize = new Size(900, 550);
            this.StartPosition = FormStartPosition.CenterScreen;
            this.Font = new Font("Segoe UI", 9);
            this.BackColor = Color.FromArgb(30, 30, 30);
            this.ForeColor = Color.White;

            BuildProcessSelector();
            BuildToolbar();
            BuildPacketsTab();
            BuildBotTab();
            BuildMapTab();
            BuildTabControl();
            BuildSendPanel();
            BuildStatusBar();

            // Layout order matters for docking
            this.Controls.Add(mainTabs);
            this.Controls.Add(processPanel);
            this.Controls.Add(toolPanel);
            this.Controls.Add(sendPanel);
            this.Controls.Add(statusLabel);

            this.Resize += (s, e) =>
            {
                processListBox.Width = this.ClientSize.Width - 80;
                injectButton.Left = refreshButton.Right + 10;
                sendTextBox.Width = this.ClientSize.Width - 160;
                sendButton.Left = sendTextBox.Right + 10;
                filterTextBox.Width = this.ClientSize.Width - 80;
                if (packetListView.Columns.Count > 3)
                    packetListView.Columns[3].Width = Math.Max(200, this.ClientSize.Width - 260);
            };
        }

        // ==================== UI BUILDERS ====================

        private void BuildProcessSelector()
        {
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
                Text = "Refresh", Size = new Size(100, 35), Location = new Point(40, 315),
                BackColor = Color.FromArgb(60, 60, 60), ForeColor = Color.White, FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 10)
            };
            refreshButton.Click += (s, e) => RefreshProcessList();

            injectButton = new Button
            {
                Text = "Inject && Connect", Size = new Size(160, 35), Location = new Point(150, 315),
                BackColor = Color.FromArgb(0, 122, 204), ForeColor = Color.White, FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 10, FontStyle.Bold)
            };
            injectButton.Click += InjectButton_Click;

            processPanel.Controls.AddRange(new Control[] { titleLabel, subtitleLabel, processListBox, refreshButton, injectButton });
        }

        private void BuildToolbar()
        {
            toolPanel = new Panel
            {
                Dock = DockStyle.Top, Height = 45,
                BackColor = Color.FromArgb(45, 45, 45),
                Padding = new Padding(8), Visible = false
            };

            disconnectButton = new Button
            {
                Text = "Disconnect", Size = new Size(100, 28), Location = new Point(8, 8),
                BackColor = Color.FromArgb(180, 50, 50), ForeColor = Color.White, FlatStyle = FlatStyle.Flat
            };
            disconnectButton.Click += DisconnectButton_Click;

            clearButton = new Button
            {
                Text = "Clear", Size = new Size(70, 28), Location = new Point(115, 8),
                BackColor = Color.FromArgb(60, 60, 60), ForeColor = Color.White, FlatStyle = FlatStyle.Flat
            };
            clearButton.Click += (s, e) => { lock (lockObj) { allPackets.Clear(); } ApplyFilter(); };

            exportButton = new Button
            {
                Text = "Export", Size = new Size(70, 28), Location = new Point(192, 8),
                BackColor = Color.FromArgb(60, 60, 60), ForeColor = Color.White, FlatStyle = FlatStyle.Flat
            };
            exportButton.Click += ExportButton_Click;

            toolPanel.Controls.AddRange(new Control[] { disconnectButton, clearButton, exportButton });
        }

        private void BuildPacketsTab()
        {
            // Filter bar
            filterPanel = new Panel
            {
                Dock = DockStyle.Top, Height = 35,
                BackColor = Color.FromArgb(40, 40, 40),
                Padding = new Padding(8, 4, 8, 4)
            };

            var filterLabel = new Label { Text = "Filter:", AutoSize = true, Location = new Point(8, 8), ForeColor = Color.LightGray };

            filterTextBox = new TextBox
            {
                Location = new Point(55, 5), Size = new Size(300, 24),
                BackColor = Color.FromArgb(50, 50, 50), ForeColor = Color.White, BorderStyle = BorderStyle.FixedSingle
            };
            filterTextBox.TextChanged += (s, e) => ApplyFilter();
            filterTextBox.PlaceholderText = "Filter packets...";

            showRecvCheckBox = new CheckBox { Text = "RECV", Checked = true, AutoSize = true, Location = new Point(370, 8), ForeColor = Color.Cyan };
            showRecvCheckBox.CheckedChanged += (s, e) => ApplyFilter();

            showSendCheckBox = new CheckBox { Text = "SEND", Checked = true, AutoSize = true, Location = new Point(440, 8), ForeColor = Color.Yellow };
            showSendCheckBox.CheckedChanged += (s, e) => ApplyFilter();

            autoScrollCheckBox = new CheckBox { Text = "Auto-scroll", Checked = true, AutoSize = true, Location = new Point(520, 8), ForeColor = Color.LightGray };

            packetCountLabel = new Label { AutoSize = true, Location = new Point(640, 8), ForeColor = Color.Gray, Text = "Packets: 0" };

            filterPanel.Controls.AddRange(new Control[] { filterLabel, filterTextBox, showRecvCheckBox, showSendCheckBox, autoScrollCheckBox, packetCountLabel });

            // Packet list
            packetListView = new ListView
            {
                Dock = DockStyle.Fill, View = View.Details, FullRowSelect = true,
                BackColor = Color.FromArgb(25, 25, 25), ForeColor = Color.White,
                Font = new Font("Consolas", 9), GridLines = true
            };
            packetListView.Columns.Add("Time", 80);
            packetListView.Columns.Add("Dir", 55);
            packetListView.Columns.Add("Opcode", 90);
            packetListView.Columns.Add("Packet", 700);

            var contextMenu = new ContextMenuStrip { BackColor = Color.FromArgb(45, 45, 45), ForeColor = Color.White };
            var copyItem = new ToolStripMenuItem("Copy Packet\tCtrl+C");
            copyItem.Click += (s, e) => CopySelectedPacket();
            var sendItem = new ToolStripMenuItem("Send This Packet");
            sendItem.Click += (s, e) => SendSelectedPacket();
            var copyToSendBox = new ToolStripMenuItem("Copy to Send Box");
            copyToSendBox.Click += (s, e) => CopyToSendBox();
            contextMenu.Items.AddRange(new ToolStripItem[] { copyItem, sendItem, new ToolStripSeparator(), copyToSendBox });
            packetListView.ContextMenuStrip = contextMenu;

            packetListView.KeyDown += (s, e) => { if (e.Control && e.KeyCode == Keys.C) { CopySelectedPacket(); e.Handled = true; } };
            packetListView.DoubleClick += (s, e) => SendSelectedPacket();
        }

        private void BuildBotTab()
        {
            botToggleButton = new Button
            {
                Text = "Start Bot", Size = new Size(140, 40), Location = new Point(15, 15),
                BackColor = Color.FromArgb(0, 160, 0), ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat, Font = new Font("Segoe UI", 11, FontStyle.Bold)
            };
            botToggleButton.Click += BotToggleButton_Click;

            botInfoLabel = new Label
            {
                Location = new Point(15, 65), Size = new Size(350, 200),
                ForeColor = Color.White, Font = new Font("Consolas", 10),
                Text = "Bot: OFF\n\nStart the bot to see live game state."
            };

            entityListView = new ListView
            {
                Location = new Point(380, 15), Size = new Size(400, 300),
                View = View.Details, FullRowSelect = true,
                BackColor = Color.FromArgb(25, 25, 25), ForeColor = Color.White,
                Font = new Font("Consolas", 8.5f), GridLines = true
            };
            entityListView.Columns.Add("Type", 55);
            entityListView.Columns.Add("VNum", 55);
            entityListView.Columns.Add("ID", 75);
            entityListView.Columns.Add("Pos", 80);
            entityListView.Columns.Add("Dist", 50);
            entityListView.Columns.Add("HP%", 45);
            entityListView.Columns.Add("Name", 120);

            // Timer for bot info updates
            botUpdateTimer = new System.Windows.Forms.Timer { Interval = 500 };
            botUpdateTimer.Tick += BotUpdateTimer_Tick;
        }

        private void BuildMapTab()
        {
            mapRenderer = new MapRenderer { Dock = DockStyle.Fill };
        }

        private void BuildTabControl()
        {
            mainTabs = new TabControl
            {
                Dock = DockStyle.Fill, Visible = false,
                Font = new Font("Segoe UI", 9.5f)
            };

            // Tab 1: Packets
            var packetsTab = new TabPage("Packets")
            {
                BackColor = Color.FromArgb(30, 30, 30)
            };
            packetsTab.Controls.Add(packetListView);
            packetsTab.Controls.Add(filterPanel);

            // Tab 2: Bot
            var botTab = new TabPage("Bot Control")
            {
                BackColor = Color.FromArgb(30, 30, 30)
            };
            botTab.Controls.AddRange(new Control[] { botToggleButton, botInfoLabel, entityListView });
            botTab.Resize += (s, e) =>
            {
                entityListView.Width = Math.Max(300, botTab.Width - 400);
                entityListView.Height = Math.Max(200, botTab.Height - 30);
                botInfoLabel.Height = Math.Max(100, botTab.Height - 80);
            };

            // Tab 3: Map
            var mapTab = new TabPage("Map")
            {
                BackColor = Color.FromArgb(20, 20, 30)
            };

            var mapFilterPanel = new Panel
            {
                Dock = DockStyle.Top, Height = 32,
                BackColor = Color.FromArgb(35, 35, 45)
            };

            var showMonstersCheck = new CheckBox { Text = "Monsters", Checked = true, AutoSize = true, Location = new Point(10, 6), ForeColor = Color.Red };
            showMonstersCheck.CheckedChanged += (s, e) => mapRenderer.ShowMonsters = showMonstersCheck.Checked;

            var showPlayersCheck = new CheckBox { Text = "Players", Checked = true, AutoSize = true, Location = new Point(110, 6), ForeColor = Color.Cyan };
            showPlayersCheck.CheckedChanged += (s, e) => mapRenderer.ShowPlayers = showPlayersCheck.Checked;

            var showNpcsCheck = new CheckBox { Text = "NPCs", Checked = true, AutoSize = true, Location = new Point(200, 6), ForeColor = Color.LightGreen };
            showNpcsCheck.CheckedChanged += (s, e) => mapRenderer.ShowNpcs = showNpcsCheck.Checked;

            var resetViewBtn = new Button
            {
                Text = "Reset View", Size = new Size(85, 22), Location = new Point(290, 4),
                BackColor = Color.FromArgb(60, 60, 60), ForeColor = Color.White, FlatStyle = FlatStyle.Flat
            };
            resetViewBtn.Click += (s, e) => mapRenderer.ResetView();

            mapFilterPanel.Controls.AddRange(new Control[] { showMonstersCheck, showPlayersCheck, showNpcsCheck, resetViewBtn });

            mapTab.Controls.Add(mapRenderer);
            mapTab.Controls.Add(mapFilterPanel);

            mainTabs.TabPages.Add(packetsTab);
            mainTabs.TabPages.Add(botTab);
            mainTabs.TabPages.Add(mapTab);
        }

        private void BuildSendPanel()
        {
            sendPanel = new Panel
            {
                Dock = DockStyle.Bottom, Height = 45,
                BackColor = Color.FromArgb(45, 45, 45),
                Padding = new Padding(8), Visible = false
            };

            var sendLabel = new Label { Text = "Send:", AutoSize = true, Location = new Point(8, 14), ForeColor = Color.LightGray };

            sendTextBox = new TextBox
            {
                Location = new Point(55, 10), Size = new Size(600, 24),
                BackColor = Color.FromArgb(50, 50, 50), ForeColor = Color.White, BorderStyle = BorderStyle.FixedSingle
            };
            sendTextBox.PlaceholderText = "Type packet(s) to send — use ; to chain multiple";
            sendTextBox.KeyDown += (s, e) => { if (e.KeyCode == Keys.Enter) { SendPacket(); e.SuppressKeyPress = true; } };

            sendButton = new Button
            {
                Text = "Send", Size = new Size(80, 28), Location = new Point(665, 8),
                BackColor = Color.FromArgb(0, 122, 204), ForeColor = Color.White, FlatStyle = FlatStyle.Flat
            };
            sendButton.Click += (s, e) => SendPacket();

            sendPanel.Controls.AddRange(new Control[] { sendLabel, sendTextBox, sendButton });
        }

        private void BuildStatusBar()
        {
            statusLabel = new Label
            {
                Dock = DockStyle.Bottom, Height = 25,
                BackColor = Color.FromArgb(60, 60, 60), ForeColor = Color.White,
                Text = "  Select a process and click Inject && Connect",
                TextAlign = ContentAlignment.MiddleLeft
            };
        }

        // ==================== PROCESS SELECTOR ====================

        private void RefreshProcessList()
        {
            processListBox.Items.Clear();
            foundProcesses = ProcessInjector.FindNosTaleProcesses();
            foreach (var proc in foundProcesses) ProcessInjector.TagWindowWithPID(proc.Pid);
            foundProcesses = ProcessInjector.FindNosTaleProcesses();

            if (foundProcesses.Count == 0)
            {
                processListBox.Items.Add("No NosTale processes found. Make sure the game is running.");
                injectButton.Enabled = false;
            }
            else
            {
                foreach (var proc in foundProcesses) processListBox.Items.Add(proc.ToString());
                processListBox.SelectedIndex = 0;
                injectButton.Enabled = true;
            }
            SetStatus($"Found {foundProcesses.Count} NosTale process(es)", Color.FromArgb(60, 60, 60));
        }

        private async void InjectButton_Click(object? sender, EventArgs e)
        {
            if (processListBox.SelectedIndex < 0 || processListBox.SelectedIndex >= foundProcesses.Count)
            { SetStatus("Select a process first", Color.FromArgb(200, 150, 0)); return; }

            var selected = foundProcesses[processListBox.SelectedIndex];
            injectButton.Enabled = false;
            refreshButton.Enabled = false;
            SetStatus($"Injecting into PID {selected.Pid}...", Color.FromArgb(200, 150, 0));

            string exeDir = AppDomain.CurrentDomain.BaseDirectory;
            string injectorPath = Path.Combine(exeDir, "Injector.exe");
            if (!File.Exists(injectorPath))
            {
                string? searchDir = Path.GetDirectoryName(exeDir.TrimEnd(Path.DirectorySeparatorChar));
                while (searchDir != null)
                {
                    string candidate = Path.Combine(searchDir, "Release", "Injector.exe");
                    if (File.Exists(candidate)) { injectorPath = candidate; break; }
                    searchDir = Path.GetDirectoryName(searchDir);
                }
            }
            if (!File.Exists(injectorPath))
            {
                SetStatus("Injector.exe not found!", Color.FromArgb(200, 50, 50));
                injectButton.Enabled = true; refreshButton.Enabled = true; return;
            }

            try
            {
                var startInfo = new System.Diagnostics.ProcessStartInfo
                {
                    FileName = injectorPath, Arguments = $"--pid {selected.Pid}",
                    UseShellExecute = true, Verb = "runas",
                    WorkingDirectory = Path.GetDirectoryName(injectorPath) ?? exeDir
                };
                var proc = System.Diagnostics.Process.Start(startInfo);
                proc?.WaitForExit(5000);
                if (proc == null || proc.ExitCode != 0)
                {
                    SetStatus($"Injection failed (exit code: {proc?.ExitCode})", Color.FromArgb(200, 50, 50));
                    injectButton.Enabled = true; refreshButton.Enabled = true; return;
                }
            }
            catch (Exception ex)
            {
                SetStatus($"Injector error: {ex.Message}", Color.FromArgb(200, 50, 50));
                injectButton.Enabled = true; refreshButton.Enabled = true; return;
            }

            SetStatus($"Injected. Connecting pipe...", Color.FromArgb(200, 150, 0));

            for (int attempt = 1; attempt <= 3; attempt++)
            {
                await System.Threading.Tasks.Task.Delay(1000);
                SetStatus($"Connecting (attempt {attempt}/3)...", Color.FromArgb(200, 150, 0));
                try
                {
                    pipeClient = new PipeClient();
                    pipeClient.PacketReceived += OnPacketReceived;
                    pipeClient.StatusReceived += OnStatusReceived;
                    pipeClient.Disconnected += OnDisconnected;
                    await pipeClient.ConnectAsync(5000);
                    ShowPacketLoggerView(selected);
                    return;
                }
                catch { pipeClient?.Dispose(); pipeClient = null; }
            }

            SetStatus("Pipe connection failed. Is the DLL console visible?", Color.FromArgb(200, 50, 50));
            injectButton.Enabled = true; refreshButton.Enabled = true;
        }

        // ==================== VIEW SWITCHING ====================

        private void ShowPacketLoggerView(NosTaleProcess proc)
        {
            this.Text = $"NosTale Packet Logger — PID {proc.Pid} — {proc.WindowTitle}";

            gameState = new GameState();
            mapRenderer.SetGameState(gameState);

            processPanel.Visible = false;
            toolPanel.Visible = true;
            mainTabs.Visible = true;
            sendPanel.Visible = true;

            botUpdateTimer.Start();
            StartCommandFileWatcher();
            SetStatus($"Connected to PID {proc.Pid} — Logging packets", Color.FromArgb(0, 150, 0));
        }

        private void ShowProcessSelectorView()
        {
            this.Text = "NosTale Packet Logger";
            processPanel.Visible = true;
            toolPanel.Visible = false;
            mainTabs.Visible = false;
            sendPanel.Visible = false;
            injectButton.Enabled = true;
            refreshButton.Enabled = true;
            RefreshProcessList();
        }

        private void DisconnectButton_Click(object? sender, EventArgs e)
        {
            botEngine?.Stop(); botEngine = null;
            botUpdateTimer.Stop();
            gameState = null;
            StopCommandFileWatcher();
            pipeClient?.Dispose(); pipeClient = null;
            ShowProcessSelectorView();
            SetStatus("Disconnected", Color.FromArgb(200, 50, 50));
        }

        // ==================== PACKET HANDLING ====================

        private void OnPacketReceived(object? sender, PacketReceivedEventArgs e)
        {
            gameState?.ProcessPacket(e.Direction, e.Packet.TrimEnd('\n', '\r'));

            var entry = new PacketEntry
            {
                Time = e.Timestamp.ToString("HH:mm:ss.fff"),
                Direction = e.Direction,
                Opcode = ExtractOpcode(e.Packet),
                Packet = e.Packet.TrimEnd('\n', '\r')
            };

            lock (lockObj) { allPackets.Add(entry); }

            if (this.IsHandleCreated)
            {
                this.BeginInvoke(() =>
                {
                    packetCountLabel.Text = $"Packets: {allPackets.Count}";
                    if (MatchesFilter(entry)) AddPacketToList(entry);
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
                this.BeginInvoke(() => { ShowProcessSelectorView(); SetStatus("Disconnected — DLL pipe closed", Color.FromArgb(200, 50, 50)); });
        }

        private void AddPacketToList(PacketEntry entry)
        {
            var item = new ListViewItem(entry.Time);
            item.SubItems.Add(entry.Direction);
            item.SubItems.Add(entry.Opcode);
            item.SubItems.Add(entry.Packet);
            item.ForeColor = entry.Direction == "RECV" ? Color.Cyan : Color.Yellow;
            packetListView.Items.Add(item);
            if (autoScrollCheckBox.Checked) packetListView.EnsureVisible(packetListView.Items.Count - 1);
        }

        private void ApplyFilter()
        {
            packetListView.BeginUpdate();
            packetListView.Items.Clear();
            List<PacketEntry> snapshot;
            lock (lockObj) { snapshot = new List<PacketEntry>(allPackets); }
            foreach (var entry in snapshot) { if (MatchesFilter(entry)) AddPacketToList(entry); }
            packetListView.EndUpdate();
        }

        private bool MatchesFilter(PacketEntry entry)
        {
            if (entry.Direction == "RECV" && !showRecvCheckBox.Checked) return false;
            if (entry.Direction == "SEND" && !showSendCheckBox.Checked) return false;
            string filter = filterTextBox.Text.Trim();
            if (string.IsNullOrEmpty(filter)) return true;
            return entry.Packet.Contains(filter, StringComparison.OrdinalIgnoreCase)
                || entry.Opcode.Contains(filter, StringComparison.OrdinalIgnoreCase);
        }

        private string ExtractOpcode(string packet)
        {
            string trimmed = packet.TrimStart();
            int idx = trimmed.IndexOf(' ');
            return idx > 0 ? trimmed.Substring(0, idx) : (trimmed.Length > 20 ? trimmed.Substring(0, 20) : trimmed);
        }

        private void SendPacket()
        {
            string text = sendTextBox.Text.Trim();
            if (string.IsNullOrEmpty(text) || pipeClient == null || !pipeClient.IsConnected) return;
            foreach (string p in text.Split(';', StringSplitOptions.RemoveEmptyEntries))
            {
                string t = p.Trim();
                if (!string.IsNullOrEmpty(t)) pipeClient.SendPacket(t);
            }
            sendTextBox.Clear(); sendTextBox.Focus();
        }

        private void ExportButton_Click(object? sender, EventArgs e)
        {
            using var dialog = new SaveFileDialog { Filter = "Log files|*.log|All|*.*", DefaultExt = "log", FileName = $"packets_{DateTime.Now:yyyyMMdd_HHmmss}.log" };
            if (dialog.ShowDialog() == DialogResult.OK)
            {
                List<PacketEntry> snapshot;
                lock (lockObj) { snapshot = new List<PacketEntry>(allPackets); }
                using var writer = new StreamWriter(dialog.FileName);
                foreach (var entry in snapshot) writer.WriteLine($"{entry.Time} [{entry.Direction}] {entry.Packet}");
                SetStatus($"Exported {snapshot.Count} packets", Color.FromArgb(0, 122, 204));
            }
        }

        private void SetStatus(string text, Color color) { statusLabel.Text = "  " + text; statusLabel.BackColor = color; }

        private void CopySelectedPacket()
        {
            if (packetListView.SelectedItems.Count == 0) return;
            string packet = packetListView.SelectedItems[0].SubItems[3].Text;
            Clipboard.SetText(packet);
            SetStatus("Copied: " + packet, Color.FromArgb(0, 122, 204));
        }

        private void SendSelectedPacket()
        {
            if (packetListView.SelectedItems.Count == 0) return;
            if (pipeClient == null || !pipeClient.IsConnected) { SetStatus("Not connected", Color.FromArgb(200, 50, 50)); return; }
            string packet = packetListView.SelectedItems[0].SubItems[3].Text;
            pipeClient.SendPacket(packet);
            SetStatus("Sent: " + packet, Color.FromArgb(0, 150, 0));
        }

        private void CopyToSendBox()
        {
            if (packetListView.SelectedItems.Count == 0) return;
            sendTextBox.Text = packetListView.SelectedItems[0].SubItems[3].Text;
            sendTextBox.Focus();
        }

        // ==================== BOT ====================

        private void BotToggleButton_Click(object? sender, EventArgs e)
        {
            if (botEngine != null && botEngine.IsRunning)
            {
                botEngine.Stop(); botEngine = null;
                botToggleButton.Text = "Start Bot";
                botToggleButton.BackColor = Color.FromArgb(0, 160, 0);
                return;
            }

            if (pipeClient == null || !pipeClient.IsConnected || gameState == null)
            { SetStatus("Not connected", Color.FromArgb(200, 50, 50)); return; }

            var packetSender = new PacketSender(pipeClient);
            botEngine = new BotEngine(gameState, packetSender);
            botEngine.OnLog += msg => { if (this.IsHandleCreated) this.BeginInvoke(() => SetStatus(msg, Color.FromArgb(0, 122, 204))); };
            botEngine.Start();
            botToggleButton.Text = "Stop Bot";
            botToggleButton.BackColor = Color.FromArgb(200, 50, 50);
        }

        private void BotUpdateTimer_Tick(object? sender, EventArgs e)
        {
            if (gameState == null) return;

            string botState = botEngine?.IsRunning == true ? botEngine.State.ToString() : "OFF";
            string info = $"=== Bot: {botState} ===\n\n";

            info += $"--- Character ---\n";
            info += $"ID: {gameState.Self.Id}\n";
            info += $"Pos: ({gameState.Self.X}, {gameState.Self.Y})  Map: {gameState.MapId}\n";
            info += $"HP: {gameState.Self.Hp}/{gameState.Self.HpMax} ({gameState.Self.HpPercent:F0}%)\n";
            info += $"MP: {gameState.Self.Mp}/{gameState.Self.MpMax} ({gameState.Self.MpPercent:F0}%)\n";
            info += $"Lv: {gameState.Self.Level}  Job Lv: {gameState.Self.JobLevel}\n";
            info += $"XP: {gameState.Self.Xp}/{gameState.Self.XpMax}\n";
            info += $"Speed: {gameState.Self.Speed}  Dead: {gameState.Self.IsDead}  Rest: {gameState.Self.IsResting}\n\n";

            var entities = gameState.Entities.Values.ToArray();
            int aliveMonsters = entities.Count(x => x.IsMonster && x.IsAlive);
            info += $"--- Map ---\n";
            info += $"Monsters: {aliveMonsters} alive  |  Players: {entities.Count(x => x.IsPlayer)}  |  NPCs: {entities.Count(x => x.IsNpc)}\n";
            info += $"Portals: {gameState.Portals.Count}\n";
            foreach (var p in gameState.Portals.Values.Take(5))
                info += $"  Portal ({p.X},{p.Y}) -> Map {p.DestMapId}\n";

            if (botEngine?.Combat?.CurrentTarget is { } target)
            {
                info += $"\n--- Target ---\n";
                info += $"M{target.VNum} (id:{target.Id}) HP:{target.HpPercent}%\n";
                info += $"Pos: ({target.X},{target.Y}) Dist: {target.DistanceTo(gameState.Self.X, gameState.Self.Y):F0}\n";
            }

            if (botEngine?.Combat?.Skills.Count > 0)
            {
                info += $"\n--- Skills ---\n";
                foreach (var s in botEngine.Combat.Skills)
                    info += $"  [{s.CastId}] {(s.IsReady ? "READY" : "CD")} range:{s.Range}\n";
            }

            botInfoLabel.Text = info;

            // Update entity list (only on Bot tab to avoid lag)
            if (mainTabs.SelectedIndex == 1)
            {
                entityListView.BeginUpdate();
                entityListView.Items.Clear();
                var sorted = entities
                    .OrderBy(x => x.DistanceTo(gameState.Self.X, gameState.Self.Y))
                    .Take(100);
                foreach (var ent in sorted)
                {
                    string type = ent.IsMonster ? "MOB" : ent.IsPlayer ? "PLR" : "NPC";
                    var item = new ListViewItem(type);
                    item.SubItems.Add(ent.VNum.ToString());
                    item.SubItems.Add(ent.Id.ToString());
                    item.SubItems.Add($"({ent.X},{ent.Y})");
                    item.SubItems.Add($"{ent.DistanceTo(gameState.Self.X, gameState.Self.Y):F0}");
                    item.SubItems.Add($"{ent.HpPercent}%");
                    item.SubItems.Add(ent.Name);
                    item.ForeColor = ent.IsMonster ? Color.Red : ent.IsPlayer ? Color.Cyan : Color.LightGreen;
                    entityListView.Items.Add(item);
                }
                entityListView.EndUpdate();
            }
        }

        // ==================== COMMAND FILE WATCHER ====================

        private void StartCommandFileWatcher()
        {
            try
            {
                if (File.Exists(CmdFilePath)) File.WriteAllText(CmdFilePath, "");
                else File.Create(CmdFilePath).Dispose();
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
            if (cmdWatcher != null) { cmdWatcher.EnableRaisingEvents = false; cmdWatcher.Dispose(); cmdWatcher = null; }
        }

        private void OnCommandFileChanged(object sender, FileSystemEventArgs e)
        {
            try
            {
                System.Threading.Thread.Sleep(100);
                string[] lines = File.ReadAllLines(CmdFilePath);
                if (lines.Length == 0) return;
                File.WriteAllText(CmdFilePath, "");
                if (pipeClient == null || !pipeClient.IsConnected) return;
                foreach (string line in lines)
                {
                    string trimmed = line.Trim();
                    if (string.IsNullOrEmpty(trimmed) || trimmed.StartsWith("#")) continue;
                    pipeClient.SendPacket(trimmed);
                    if (this.IsHandleCreated)
                        this.BeginInvoke(() => SetStatus($"Cmd: {trimmed}", Color.FromArgb(0, 150, 0)));
                }
            }
            catch { }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            botEngine?.Stop();
            botUpdateTimer?.Stop();
            StopCommandFileWatcher();
            pipeClient?.Dispose();
            base.OnFormClosing(e);
        }
    }
}
