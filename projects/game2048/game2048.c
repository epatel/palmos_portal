#ifdef __GNUC__
#    include <PalmOS.h>
#endif

#define MainForm    1000
#define GameOverAlt 1000

// Grid layout
#define GRID_X      4
#define GRID_Y      30
#define CELL_W      36
#define CELL_H      30
#define CELL_GAP    2
#define GRID_COLS   4
#define GRID_ROWS   4

// Swipe threshold
#define SWIPE_MIN   10

// Game state
static Int16 board[4][4];
static Int32 score;
static Int16 penStartX, penStartY;
static Boolean penTracking;

// Simple random using SysRandom
static Int16 randN(Int16 n) {
    return SysRandom(0) % n;
}

// --- Drawing ---

static void drawRect(Int16 x, Int16 y, Int16 w, Int16 h, Boolean fill) {
    RectangleType r;
    r.topLeft.x = x; r.topLeft.y = y;
    r.extent.x = w; r.extent.y = h;
    if (fill) WinDrawRectangle(&r, 0);
    else WinEraseRectangle(&r, 0);
}

static void drawFrame(Int16 x, Int16 y, Int16 w, Int16 h) {
    RectangleType r;
    r.topLeft.x = x; r.topLeft.y = y;
    r.extent.x = w; r.extent.y = h;
    WinDrawRectangleFrame(simpleFrame, &r);
}

static void drawScore() {
    Char buf[32];
    drawRect(0, 15, 160, 13, false);
    StrPrintF(buf, "Score: %ld", score);
    WinDrawChars(buf, StrLen(buf), 4, 16);
}

static void drawCell(Int16 row, Int16 col) {
    Int16 x, y, val, tw;
    Char buf[8];
    RectangleType r;

    x = GRID_X + col * (CELL_W + CELL_GAP);
    y = GRID_Y + row * (CELL_H + CELL_GAP);
    val = board[row][col];

    // Clear cell area
    drawRect(x, y, CELL_W, CELL_H, false);

    if (val == 0) {
        // Empty cell: just a border
        drawFrame(x, y, CELL_W, CELL_H);
        return;
    }

    // Format number
    StrPrintF(buf, "%d", val);
    tw = FntCharsWidth(buf, StrLen(buf));

    if (val >= 128) {
        // Inverted tile: black fill, white text
        drawRect(x, y, CELL_W, CELL_H, true);
        // Draw text then invert the text area to make it white-on-black
        r.topLeft.x = x + (CELL_W - tw) / 2;
        r.topLeft.y = y + (CELL_H - 11) / 2;
        r.extent.x = tw;
        r.extent.y = 11;
        WinInvertRectangle(&r, 0);
        WinDrawChars(buf, StrLen(buf), r.topLeft.x, r.topLeft.y);
        WinInvertRectangle(&r, 0);
    } else {
        // Normal tile: white fill, black text, border
        drawFrame(x, y, CELL_W, CELL_H);
        WinDrawChars(buf, StrLen(buf),
                     x + (CELL_W - tw) / 2,
                     y + (CELL_H - 11) / 2);
    }
}

static void drawBoard() {
    Int16 r, c;
    drawScore();
    for (r = 0; r < GRID_ROWS; r++)
        for (c = 0; c < GRID_COLS; c++)
            drawCell(r, c);
}

// --- Game Logic ---

static Int16 countEmpty() {
    Int16 r, c, n;
    n = 0;
    for (r = 0; r < GRID_ROWS; r++)
        for (c = 0; c < GRID_COLS; c++)
            if (board[r][c] == 0) n++;
    return n;
}

static void spawnTile() {
    Int16 r, c, idx, n;
    n = countEmpty();
    if (n == 0) return;
    idx = randN(n);
    for (r = 0; r < GRID_ROWS; r++)
        for (c = 0; c < GRID_COLS; c++)
            if (board[r][c] == 0) {
                if (idx == 0) {
                    board[r][c] = (randN(10) < 9) ? 2 : 4;
                    return;
                }
                idx--;
            }
}

static void initGame() {
    Int16 r, c;
    for (r = 0; r < GRID_ROWS; r++)
        for (c = 0; c < GRID_COLS; c++)
            board[r][c] = 0;
    score = 0;
    penTracking = false;
    SysRandom(TimGetTicks());
    spawnTile();
    spawnTile();
}

static Boolean canMove() {
    Int16 r, c;
    for (r = 0; r < GRID_ROWS; r++)
        for (c = 0; c < GRID_COLS; c++) {
            if (board[r][c] == 0) return true;
            if (c < 3 && board[r][c] == board[r][c+1]) return true;
            if (r < 3 && board[r][c] == board[r+1][c]) return true;
        }
    return false;
}

// Slide and merge a single row/column (4 elements) toward index 0
// Returns true if anything changed
static Boolean slideLine(Int16 *line) {
    Int16 tmp[4];
    Int16 i, pos;
    Boolean changed;

    changed = false;

    // Compact: remove zeros
    pos = 0;
    for (i = 0; i < 4; i++)
        if (line[i] != 0)
            tmp[pos++] = line[i];
    while (pos < 4)
        tmp[pos++] = 0;

    // Merge adjacent equals
    for (i = 0; i < 3; i++) {
        if (tmp[i] != 0 && tmp[i] == tmp[i+1]) {
            tmp[i] = tmp[i] * 2;
            score += tmp[i];
            tmp[i+1] = 0;
            changed = true;
        }
    }

    // Compact again
    pos = 0;
    for (i = 0; i < 4; i++)
        if (tmp[i] != 0) {
            if (line[pos] != tmp[i]) changed = true;
            line[pos++] = tmp[i];
        }
    while (pos < 4) {
        if (line[pos] != 0) changed = true;
        line[pos++] = 0;
    }

    return changed;
}

static Boolean moveLeft() {
    Int16 r;
    Boolean moved;
    moved = false;
    for (r = 0; r < 4; r++)
        if (slideLine(board[r])) moved = true;
    return moved;
}

static Boolean moveRight() {
    Int16 r, c;
    Int16 line[4];
    Boolean moved;
    moved = false;
    for (r = 0; r < 4; r++) {
        // Reverse row into line
        for (c = 0; c < 4; c++) line[c] = board[r][3 - c];
        if (slideLine(line)) {
            moved = true;
            for (c = 0; c < 4; c++) board[r][3 - c] = line[c];
        }
    }
    return moved;
}

static Boolean moveUp() {
    Int16 r, c;
    Int16 line[4];
    Boolean moved;
    moved = false;
    for (c = 0; c < 4; c++) {
        for (r = 0; r < 4; r++) line[r] = board[r][c];
        if (slideLine(line)) {
            moved = true;
            for (r = 0; r < 4; r++) board[r][c] = line[r];
        }
    }
    return moved;
}

static Boolean moveDown() {
    Int16 r, c;
    Int16 line[4];
    Boolean moved;
    moved = false;
    for (c = 0; c < 4; c++) {
        for (r = 0; r < 4; r++) line[r] = board[3 - r][c];
        if (slideLine(line)) {
            moved = true;
            for (r = 0; r < 4; r++) board[3 - r][c] = line[r];
        }
    }
    return moved;
}

static void doMove(Int16 dx, Int16 dy) {
    Boolean moved;
    Int16 adx, ady;

    adx = dx; if (adx < 0) adx = -adx;
    ady = dy; if (ady < 0) ady = -ady;

    if (adx < SWIPE_MIN && ady < SWIPE_MIN) return;

    if (adx > ady) {
        moved = (dx < 0) ? moveLeft() : moveRight();
    } else {
        moved = (dy < 0) ? moveUp() : moveDown();
    }

    if (moved) {
        spawnTile();
        drawBoard();
        if (!canMove()) {
            FrmAlert(GameOverAlt);
            initGame();
            drawBoard();
        }
    }
}

// --- Event Handling ---

static Boolean mainFormEventHandler(EventPtr pEvent) {
    FormPtr pForm;

    switch (pEvent->eType) {
    case frmOpenEvent:
        pForm = FrmGetActiveForm();
        FrmDrawForm(pForm);
        initGame();
        drawBoard();
        return true;

    case penDownEvent:
        penStartX = pEvent->screenX;
        penStartY = pEvent->screenY;
        penTracking = true;
        return true;

    case penUpEvent:
        if (penTracking) {
            penTracking = false;
            doMove(pEvent->screenX - penStartX,
                   pEvent->screenY - penStartY);
        }
        return true;

    default:
        break;
    }
    return false;
}

static Boolean appHandleEvent(EventPtr pEvent) {
    FormPtr pForm;
    if (pEvent->eType == frmLoadEvent) {
        pForm = FrmInitForm(pEvent->data.frmLoad.formID);
        FrmSetActiveForm(pForm);
        FrmSetEventHandler(pForm, mainFormEventHandler);
        return true;
    }
    return false;
}

UInt32 PilotMain(UInt16 cmd, void *cmdPBP, UInt16 launchFlags) {
    EventType event;
    if (cmd == sysAppLaunchCmdNormalLaunch) {
        FrmGotoForm(MainForm);
        do {
            EvtGetEvent(&event, evtWaitForever);
            if (!SysHandleEvent(&event))
            if (!appHandleEvent(&event))
                FrmDispatchEvent(&event);
        } while (event.eType != appStopEvent);
        FrmCloseAllForms();
    }
    return 0;
}
