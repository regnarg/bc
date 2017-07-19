unitsize(1cm);

void img(bool cross) {
    int x2 = 3;
    draw((0,0.5)--(0,-4.5), scale(2)*defaultpen);
    draw((x2,0.5)--(x2,-4.5), scale(2)*defaultpen);
    for (int i = 0; i < 4; ++i) {
        if (cross) {
            draw((0,-i)--(x2,-i-1), Arrow);
            draw((x2,-i)--(0,-i-1), Arrow);
        } else {
            draw((0,-i)--(x2,-i-.5), Arrow);
            draw((x2,-i-.5)--(0,-i-1), Arrow);
        }
    }
    for (int i = 0; i < 5; ++i) {
        draw((0,-i)--(x2,-i), dashed+scale(0.5)*currentpen);
    }
}

img(false);
label("(a) request-response",(1.5, -5), S);
draw((-.3,0)--(-.1,0));
draw((-.3,-1)--(-.1,-1));
draw((-.2, 0)--(-.2,-1), Arrows);
label("RTT", (-.3, -.5), W);
currentpicture = shift((-5, 0))*currentpicture;
img(true);
label("(b) ``criss-cross''",(1.5, -5), S);
draw((3.3,0)-- (3.1,0));
draw((3.3,-2)--(3.1,-2));
draw((3.2, 0)--(3.2,-2), Arrows);
label("RTT", (3.3, -1), E);
