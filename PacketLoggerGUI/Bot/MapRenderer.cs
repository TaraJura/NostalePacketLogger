using System;
using System.Drawing;
using System.Linq;
using System.Windows.Forms;

namespace PacketLoggerGUI.Bot
{
    public class MapRenderer : Panel
    {
        private GameState? _gameState;
        private float _scale = 3.0f;
        private int _offsetX = 0;
        private int _offsetY = 0;
        private Point _dragStart;
        private bool _dragging;
        private System.Windows.Forms.Timer _refreshTimer;

        // Filter checkboxes (created externally, referenced here)
        public bool ShowMonsters { get; set; } = true;
        public bool ShowPlayers { get; set; } = true;
        public bool ShowNpcs { get; set; } = true;

        public MapRenderer()
        {
            this.DoubleBuffered = true;
            this.BackColor = Color.FromArgb(20, 20, 30);
            this.SetStyle(ControlStyles.Selectable, true);
            this.TabStop = true;

            this.MouseEnter += (s, e) => this.Focus();
            this.MouseDown += OnMouseDown;
            this.MouseUp += OnMouseUp;
            this.MouseMove += OnMouseMove;

            _refreshTimer = new System.Windows.Forms.Timer { Interval = 200 };
            _refreshTimer.Tick += (s, e) => this.Invalidate();
            _refreshTimer.Start();
        }

        protected override void OnMouseWheel(MouseEventArgs e)
        {
            base.OnMouseWheel(e);
            if (e.Delta > 0)
                _scale = Math.Min(_scale * 1.25f, 25f);
            else
                _scale = Math.Max(_scale / 1.25f, 0.3f);
            Invalidate();
        }

        public void SetGameState(GameState state)
        {
            _gameState = state;
        }

        public void ResetView()
        {
            _offsetX = 0;
            _offsetY = 0;
            _scale = 3.0f;
            Invalidate();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            var g = e.Graphics;
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;

            if (_gameState == null)
            {
                g.DrawString("Not connected — inject into a game first", this.Font, Brushes.Gray, 10, 10);
                return;
            }

            int playerX = _gameState.Self.X;
            int playerY = _gameState.Self.Y;
            float cx = this.Width / 2f + _offsetX;
            float cy = this.Height / 2f + _offsetY;

            // Grid
            using var gridPen = new Pen(Color.FromArgb(25, 80, 80, 100));
            int gridStep = _scale > 5 ? 5 : (_scale > 2 ? 10 : 20);
            int gridRange = (int)(Math.Max(this.Width, this.Height) / _scale) + gridStep * 2;
            int gridStart = -gridRange / 2;
            for (int i = gridStart; i < gridRange; i += gridStep)
            {
                int wx = playerX + i - (playerX % gridStep);
                int wy = playerY + i - (playerY % gridStep);
                float sx = cx + (wx - playerX) * _scale;
                float sy = cy + (wy - playerY) * _scale;
                g.DrawLine(gridPen, sx, 0, sx, this.Height);
                g.DrawLine(gridPen, 0, sy, this.Width, sy);
            }

            // Coordinate labels on grid
            using var coordFont = new Font("Consolas", 7f);
            using var coordBrush = new SolidBrush(Color.FromArgb(60, 150, 150, 150));
            if (_scale > 1.5f)
            {
                for (int i = gridStart; i < gridRange; i += gridStep * 2)
                {
                    int wx = playerX + i - (playerX % (gridStep * 2));
                    float sx = cx + (wx - playerX) * _scale;
                    if (sx > 0 && sx < this.Width)
                        g.DrawString(wx.ToString(), coordFont, coordBrush, sx + 2, this.Height - 14);

                    int wy = playerY + i - (playerY % (gridStep * 2));
                    float sy = cy + (wy - playerY) * _scale;
                    if (sy > 0 && sy < this.Height)
                        g.DrawString(wy.ToString(), coordFont, coordBrush, 2, sy + 2);
                }
            }

            // Entities
            var entities = _gameState.Entities.Values.ToArray();
            foreach (var entity in entities)
            {
                // Filter
                if (entity.IsMonster && !ShowMonsters) continue;
                if (entity.IsPlayer && !ShowPlayers) continue;
                if (entity.IsNpc && !ShowNpcs) continue;

                float ex = cx + (entity.X - playerX) * _scale;
                float ey = cy + (entity.Y - playerY) * _scale;

                if (ex < -30 || ex > this.Width + 30 || ey < -30 || ey > this.Height + 30)
                    continue;

                Color color;
                float size;
                string label;

                if (entity.IsMonster)
                {
                    color = entity.HpPercent > 0 ? Color.Red : Color.DarkRed;
                    size = Math.Max(4, 6 * (_scale / 3f));
                    label = $"M{entity.VNum}";
                }
                else if (entity.IsPlayer)
                {
                    color = Color.FromArgb(0, 200, 255);
                    size = Math.Max(5, 7 * (_scale / 3f));
                    label = entity.Name;
                }
                else
                {
                    color = Color.FromArgb(0, 200, 0);
                    size = Math.Max(3, 5 * (_scale / 3f));
                    label = entity.Name.Length > 0 ? entity.Name : $"N{entity.VNum}";
                }

                size = Math.Min(size, 14);

                using var brush = new SolidBrush(color);
                g.FillEllipse(brush, ex - size / 2, ey - size / 2, size, size);

                // Labels for nearby or zoomed in
                float dist = (float)entity.DistanceTo(playerX, playerY);
                if (_scale > 2f && (dist < 40 || entity.IsPlayer))
                {
                    using var labelBrush = new SolidBrush(Color.FromArgb(180, color));
                    using var labelFont = new Font("Segoe UI", Math.Min(8f, 6f + _scale * 0.3f));
                    g.DrawString(label, labelFont, labelBrush, ex + size, ey - 5);
                }
            }

            // Player dot (always center, always on top)
            float pSize = Math.Min(12, Math.Max(6, 8 * (_scale / 3f)));
            using var playerBrush = new SolidBrush(Color.Yellow);
            g.FillEllipse(playerBrush, cx - pSize / 2, cy - pSize / 2, pSize, pSize);
            using var playerOutline = new Pen(Color.White, 1.5f);
            g.DrawEllipse(playerOutline, cx - pSize / 2, cy - pSize / 2, pSize, pSize);

            // HUD overlay
            using var hudFont = new Font("Consolas", 9.5f);
            using var hudBg = new SolidBrush(Color.FromArgb(160, 10, 10, 20));
            using var hudText = new SolidBrush(Color.FromArgb(220, 230, 230, 230));

            g.FillRectangle(hudBg, 4, 4, 380, 54);
            g.DrawString($"Map: {_gameState.MapId}  |  Pos: ({playerX}, {playerY})  |  Zoom: {_scale:F1}x", hudFont, hudText, 8, 8);
            g.DrawString($"HP: {_gameState.Self.Hp}/{_gameState.Self.HpMax}  |  MP: {_gameState.Self.Mp}/{_gameState.Self.MpMax}", hudFont, hudText, 8, 26);

            int mc = entities.Count(x => x.IsMonster && ShowMonsters);
            int pc = entities.Count(x => x.IsPlayer && ShowPlayers);
            int nc = entities.Count(x => x.IsNpc && ShowNpcs);
            g.DrawString($"Visible: {mc} mobs, {pc} players, {nc} npcs", hudFont, hudText, 8, 44);

            // Legend
            int ly = this.Height - 22;
            g.FillRectangle(hudBg, 4, ly - 4, 250, 22);
            using var legFont = new Font("Segoe UI", 7.5f);
            g.FillEllipse(Brushes.Yellow, 8, ly, 8, 8);
            g.DrawString("You", legFont, hudText, 20, ly - 1);
            if (ShowMonsters) { g.FillEllipse(Brushes.Red, 55, ly, 8, 8); g.DrawString("Monster", legFont, hudText, 67, ly - 1); }
            if (ShowPlayers) { g.FillEllipse(Brushes.Cyan, 125, ly, 8, 8); g.DrawString("Player", legFont, hudText, 137, ly - 1); }
            if (ShowNpcs) { g.FillEllipse(Brushes.Green, 190, ly, 8, 8); g.DrawString("NPC", legFont, hudText, 202, ly - 1); }
        }

        private void OnMouseDown(object? sender, MouseEventArgs e)
        {
            this.Focus();
            if (e.Button == MouseButtons.Left)
            {
                _dragging = true;
                _dragStart = e.Location;
            }
            else if (e.Button == MouseButtons.Middle)
            {
                ResetView();
            }
        }

        private void OnMouseUp(object? sender, MouseEventArgs e)
        {
            _dragging = false;
        }

        private void OnMouseMove(object? sender, MouseEventArgs e)
        {
            if (_dragging)
            {
                _offsetX += e.X - _dragStart.X;
                _offsetY += e.Y - _dragStart.Y;
                _dragStart = e.Location;
                Invalidate();
            }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing) { _refreshTimer?.Stop(); _refreshTimer?.Dispose(); }
            base.Dispose(disposing);
        }
    }
}
