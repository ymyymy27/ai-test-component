package com.demo;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/orders")
public class OrderController {
    @GetMapping("")
    public String list() {
        return "[]";
    }

    @PostMapping("")
    public String create() {
        return "{\"id\":1}";
    }

    @DeleteMapping("/{id}")
    public void remove(@PathVariable Long id) {}
}
