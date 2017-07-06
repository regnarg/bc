unitsize(1cm);
dotfactor=8;
defaultpen(fontsize(12));

picture histgraph(string node, int lastver) {
    picture oldpic = currentpicture;
    picture newpic = currentpicture = new picture;

    pair u = (0,0);
    pair v = (0,1);
    pair w = (0,2);
    pair w1 = (0,1) + rotate(30)*(0,1);
    pair w2 = (0,1) + rotate(-30)*(0,1);
    pair z = reflect(w1, w2) * v;
    dot(u);
    label("${\bf "+node+"}$", u, 2*S);
    label("$u$", u, E);
    if (lastver >= 1) {
        dot(v);
        label("$v$", v, E);
        draw(v--u);
    }
    if (lastver == 2) {
        dot(w);
        label("$w_" + (node == "A" ? "1" : "2") + "$", w, E);
        draw(w--v);
    }
    if (lastver >= 3) {
        dot(w1);
        dot(w2);
        draw(w1--v);
        draw(w2--v);
        label("$w_1$", w1, W);
        label("$w_2$", w2, E);
    }
    if (lastver >= 4) {
        dot(z);
        label("$z$", z, E);
        draw(z--w1);
        draw(z--w2);
    }

    currentpicture = oldpic;
    return newpic;
}

picture both(int aver, int bver, string description) {
    picture oldpic = currentpicture;
    picture newpic = currentpicture = new picture;

    picture pic1 = histgraph("A", aver);
    picture pic2 = histgraph("B", bver);

    real off = bver >= 3 ? 2.5 : 1.5;
    add(pic1);
    add(shift(off)*pic2);
    label(description, (off/2, -.7), S);

    currentpicture = oldpic;
    return newpic;
}

add(both(1, 1, "In the beginning"));

add(shift(4.5)*both(2, 2, "After edits on both sides"));
add(shift(9)*both(3, 3, "After re-synchronization"));

add(shift((2,-5))*both(3, 4, "After resolution on B"));
add(shift((7.5,-5))*both(4, 4, "After re-synchronization"));

