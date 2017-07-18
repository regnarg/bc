unitsize(.5cm);
real width = 10;
pair to_left = (-width/2, -1);
pair to_right = (width/2, -1);
pair down = (0,-1);

int hI = 3;
int hII = 11;

pair vertpos(string s) {
    pair cur = (0,0);
    real sc = 1;
    for (int i = 0; i < length(s); ++i) {
        string c = substr(s, i, 1);
        if (i >= hI) cur += down;
        else if (c == "0") cur += xscale(sc)*to_left;
        else cur += xscale(sc)*to_right;
        sc /= 2;
    }
    return cur;
}

path vertpath(string s, int minlevel=0, int maxlevel=9999) {
    pair cur = (0,0);
    path ret;
    real sc = 1;
    for (int i = 0; i < length(s); ++i) {
        dot(cur);
        if (i == minlevel)  { ret = cur; }
        string c = substr(s, i, 1);
        if (i >= hI) cur += down;
        else if (c == "0") cur += xscale(sc)*to_left;
        else cur += xscale(sc)*to_right;
        if (i >= minlevel && i <= maxlevel + 1)
            ret = ret -- cur;
        dot(cur);
        sc /= 2;
    }
    return ret;
}

real side_sc = 1/8;
void drawside(string s, int minlevel=hI, int maxlevel=9999) {
    pair cur = (0,0);
    path ret;
    real sc=1;
    for (int i = 0; i < length(s); ++i) {
        if (i == minlevel)  { ret = cur; }
        string c = substr(s, i, 1);
        if (i >= hI) {
            if (i >= minlevel && i <= maxlevel + 1){
                pair side = cur + xscale(side_sc)*(c == "0" ? to_right: to_left);
                draw(cur--side, scale(0.65)*defaultpen);
                draw(side, scale(0.65*dotfactor)*currentpen);
                real olddf = dotfactor;
            }
            cur += down;
        }
        else if (c == "0") cur += xscale(sc)*to_left;
        else cur += xscale(sc)*to_right;
        dot(cur);
        sc /= 2;
    }

}

int bottom = 16;

string[] changes = {
"000100001000",
"0100000001001",
"10011110110",
"110100100111",
"00101101111",
"011010101110",
"10100110100",
"11101011101111"
};

pen div_pen = scale(3)*defaultpen + 0.8*white;
void divider(real y) {
    draw( (-10, y)--(18.5,y), div_pen);
}
real[] divs = {.5, -hI-.5, -hII-.5, -bottom-1.8};
for (real div: divs) divider(div);

void meas(real x, int adiv, int bdiv, string lbl) {
    draw((x, divs[adiv])--(x, divs[bdiv]), Arrows);
    label(lbl, (x, (divs[adiv]+divs[bdiv])/2), E, fontsize(14));
}

meas(13, 0, 1, "$\lg c$");
meas(15, 0, 2, "$\lg n$");
meas(17, 0, 3, "$\ell$");


pen sl_lbl = 0.4white + fontsize(18);
real sl_lbl_x = 11;
label("I",  (sl_lbl_x, -(hI)/2), sl_lbl);
label("II", (sl_lbl_x, -hI-(hII-2)/2), sl_lbl);
label("III", (sl_lbl_x, -((hI+hII+bottom-2)/2)), sl_lbl);


for (string s : changes) {
    draw(vertpath(s), scale(1.2)*currentpen);
    drawside(s);
    pair bot = (vertpos(s).x, -bottom);
    draw(vertpos(s)--bot, scale(2.5)*dotted);
    //draw(shift(vertpos(s))*scale(.2)*unitcircle);
    draw(vertpos(s), scale(1.25*dotfactor)*currentpen);
    dot(bot);
}

draw(brace((-9.5, -bottom-.2), (9.5,-bottom-.2), -.6));
label("changes (new leaves) on both sides", (0, -bottom-.5), S);
