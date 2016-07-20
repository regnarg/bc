unitsize(1cm);

real W = 10;
real H = 1;

int groups = 10;
real GW = W/groups;
real inodefrac = 0.15;
real IW = GW*inodefrac;

for (int i = 0; i < groups; ++i) {
    draw((i*GW,0)--((i+1)*GW,0)--((i+1)*GW,H)--(i*GW,H)--cycle);
    fill((i*GW,0)--(i*GW+IW,0)--(i*GW+IW,H)--(i*GW,H)--cycle, gray);
    draw((i*GW,0)--(i*GW+IW,0)--(i*GW+IW,H)--(i*GW,H)--cycle);
}

draw(brace((0,0), (GW,0), -0.25));
label("Block group", (GW/2, -0.25), S);

draw((0,0)--(W,0)--(W,H)--(0,H)--cycle);
